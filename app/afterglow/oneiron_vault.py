"""Client for an Oneiron vault's facade (/v1/core/facade/<verb>: vault.witness, vault.recall), as a paired agent.

Each request carries the agent's capability slip (Authorization: Bearer v2.slip.<hex>) and a fresh
holder proof (x-oneiron-binding): {"timestamp", "nonce", "signature"}, where the signature is the
agent's Ed25519 key over the slip's binding transcript

    b"oneiron/slip-binding/v2\\0" || BLAKE3(slip bytes) || u64be(len(challenge)) || challenge
    challenge = "oneiron-request:{timestamp}:{nonce}"

(the engine's authority::holder_proof). A hosted vault behind Cloudflare Access also needs the
service-token headers and a non-default User-Agent.

Credentials come from the environment, never from this repo:
  AFTERGLOW_ONEIRON_CREDENTIAL         path to the paired agent's JSON ({"credential": "v2.cred.<slip>.<seed>",
                                       "origin": ...}): Afterglow's own (companion) credential
  AFTERGLOW_ONEIRON_PERSON_CREDENTIAL_TEMPLATE  path of the user's PERSON-bound credential, with {world} in it
                                       (the vault names a turn's speaker by the credential that sent it)
  AFTERGLOW_CF_ACCESS_CLIENT_ID / _SECRET   the Access service token (hosted vaults)
Needs the "oneiron" extra: uv run --extra oneiron ...
"""
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request

USER_AGENT = "afterglow-app/0.1 (+https://github.com/oneiron-dev/muse-glimmer-afterglow)"
WITNESS_TIMEOUT_S = 90  # a busy vault can take longer than 30 s to commit a witnessed turn
WITNESS_TRIES = 3


class VaultError(RuntimeError):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status, self.body = status, body


class OneironVault:
    def __init__(self, credential_path=None, origin=None):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        import blake3

        path = credential_path or os.environ.get("AFTERGLOW_ONEIRON_CREDENTIAL")
        if not path:
            raise RuntimeError("set AFTERGLOW_ONEIRON_CREDENTIAL to the paired agent's credential file")
        doc = json.loads(open(path).read())
        prefix, _, rest = doc["credential"].partition("v2.cred.")
        slip_hex, _, seed_hex = rest.rpartition(".")
        if prefix or not slip_hex or len(seed_hex) != 64:
            raise RuntimeError("the credential is not v2.cred.<slip>.<seed>")
        self.origin = (origin or doc.get("origin")).rstrip("/")
        self.bearer = f"Bearer v2.slip.{slip_hex}"
        self.key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(seed_hex))
        self.slip_digest = blake3.blake3(bytes.fromhex(slip_hex)).digest()
        self.access = {}
        if os.environ.get("AFTERGLOW_CF_ACCESS_CLIENT_ID"):
            self.access = {"CF-Access-Client-Id": os.environ["AFTERGLOW_CF_ACCESS_CLIENT_ID"],
                           "CF-Access-Client-Secret": os.environ["AFTERGLOW_CF_ACCESS_CLIENT_SECRET"]}
        self.skew = 0  # seconds added to the wall clock so proofs fall inside the vault's 60 s window
        self.probes = 0  # clock probes so far; a 401 after the first probe may mean the vault's clock moved

    def holder_proof(self):
        timestamp = int(time.time()) + self.skew
        nonce = secrets.token_hex(16)
        challenge = f"oneiron-request:{timestamp}:{nonce}".encode()
        transcript = (b"oneiron/slip-binding/v2\0" + self.slip_digest
                      + len(challenge).to_bytes(8, "big") + challenge)
        return json.dumps({"timestamp": timestamp, "nonce": nonce, "signature": self.key.sign(transcript).hex()},
                          separators=(",", ":"))

    def _post(self, path, body, timeout=WITNESS_TIMEOUT_S):
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT, "Authorization": self.bearer,
                   "x-oneiron-binding": self.holder_proof(), **self.access}
        req = urllib.request.Request(self.origin + path, data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text = r.read()
                return json.loads(text) if text else None
        except urllib.error.HTTPError as e:
            raise VaultError(e.code, e.read().decode(errors="replace")) from e

    def facade(self, verb, body, timeout=WITNESS_TIMEOUT_S):
        """POST one facade verb. A 401 on the first call probes the vault's proof clock once (its authority
        clock can trail the wall clock) and retries with the offset it finds."""
        try:
            return self._post(f"/v1/core/facade/{verb}", body, timeout)
        except VaultError as e:
            if e.status != 401 or self.probes >= 3:
                raise
            first = e
        self.probes += 1
        for step in range(1, 11):
            for skew in (-60 * step, 60 * step):
                self.skew = skew
                try:
                    return self._post(f"/v1/core/facade/{verb}", body, timeout)
                except VaultError as e:
                    if e.status != 401:
                        raise
        self.skew = 0
        raise VaultError(401, f"no holder proof within 10 min of the wall clock was accepted; first reply: {first.body[:200]}")

    # --- the verbs the notes backend uses ---
    def witness_run(self, conversation_ref, author, texts, occurred_at, ids=None):
        """One witnessed turn: consecutive messages of one speaker. The vault takes the speaker from the
        authenticated caller, so call this on the vault whose credential is that speaker.

        `ids` are the messages' 32-hex entity ids. The vault admits a message id once, so with ids a timed-out
        call is resent unchanged (same ids) and cannot land twice; without them the vault makes new ids."""
        messages = [{"author": author, "message_type": "text", "content": t, "is_visible": True, "order": i}
                    for i, t in enumerate(texts)]
        for m, mid in zip(messages, ids or []):
            m["id"] = mid
        body = {"conversation_ref": conversation_ref, "occurred_at": int(occurred_at), "messages": messages}
        for attempt in range(1, WITNESS_TRIES + 1):
            try:
                return self.facade("vault.witness", body)
            except (TimeoutError, urllib.error.URLError) as e:
                if not ids or attempt == WITNESS_TRIES or isinstance(e, urllib.error.HTTPError):
                    raise
                print(f"  vault.witness timed out ({e}); resending the same message ids", file=sys.stderr, flush=True)

    def client_for(self, author):
        return self

    def witness(self, conversation_ref, messages, occurred_at=None):
        """messages: [(author, text)] in order; author "user" or "assistant" (the vault calls it "companion").
        Every run is sent with this one credential. Returns one result per run."""
        at = occurred_at or time.time()
        return [self.witness_run(conversation_ref, a, texts, at) for a, texts in speaker_runs(messages)]


def speaker_runs(messages):
    """[(author, text)] -> [(vault author, [texts])]: one entry per consecutive run of one speaker."""
    runs = []
    for author, text in messages:
        author = "companion" if author == "assistant" else author
        if runs and runs[-1][0] == author:
            runs[-1][1].append(text)
        else:
            runs.append((author, [text]))
    return runs


class SpeakerVaults:
    """One vault client per speaker: user runs go with the user's PERSON-bound credential, companion runs with
    Afterglow's agent credential, so the vault records who said what (and the Dreamer has a subject)."""

    def __init__(self, person_credential_path, agent_credential_path=None):
        self.by_author = {"user": OneironVault(person_credential_path),
                          "companion": OneironVault(agent_credential_path)}
        self.origin = self.by_author["companion"].origin

    def client_for(self, author):
        return self.by_author[author]

    def witness_run(self, conversation_ref, author, texts, occurred_at, ids=None):
        return self.by_author[author].witness_run(conversation_ref, author, texts, occurred_at, ids)

    def witness(self, conversation_ref, messages, occurred_at=None):
        at = occurred_at or time.time()
        return [self.by_author[a].witness_run(conversation_ref, a, texts, at) for a, texts in speaker_runs(messages)]

    def recall(self, query, limit=5, effort="light"):
        return self.by_author["companion"].recall(query, limit=limit, effort=effort)

    def recall(self, query, limit=5, effort="light"):
        return self.facade("vault.recall", {"query": query, "effort": effort, "limit": limit})


def push_day(world, day, vault=None, from_turn=None, person_credential=None):
    """Witness a recorded day (runs/<world>/<day>/daylog.jsonl) into the vault: per turn, the user's message,
    one assistant message per saved note, and the answer. Progress goes to runs/<world>/<day>/oneiron.json after
    every turn, and a rerun skips the turns recorded there (or starts at from_turn)."""
    import datetime as dt

    from . import paths
    from .notes import conversation_ref, stable_id

    if vault is None:
        vault = SpeakerVaults(person_credential) if person_credential else OneironVault()
    speakers = "person+agent" if isinstance(vault, SpeakerVaults) else "agent"
    version = 2 if speakers == "person+agent" else 1
    d = paths.day_dir(world, day)
    records = [json.loads(line) for line in (d / "daylog.jsonl").read_text().splitlines() if line.strip()]
    conv = conversation_ref(world, day, version)
    out_path = d / "oneiron.json"
    prev = json.loads(out_path.read_text()) if out_path.exists() else {}
    if prev and (prev.get("world"), prev.get("day"), prev.get("vault")) != (world, day, vault.origin):
        raise SystemExit(f"{out_path} records {prev.get('world')} day {prev.get('day')} in {prev.get('vault')}, "
                         f"not {world} day {day} in {vault.origin}; move it aside to push here")
    history = prev.get("history", [])
    if prev and prev.get("conversation_ref") != conv:  # an earlier push into another conversation stays on record
        history.append({k: prev.get(k) for k in ("conversation_ref", "speakers", "pushed_at", "turns", "messages")})
        prev = {}
    receipts = prev.get("receipts", [])
    # A turn is done when every one of its speaker runs has a receipt; receipts from before per-run tracking
    # (no "runs" field) count as done. A partial turn resumes at its first unsent run.
    by_turn = {r["turn"]: r for r in receipts}
    done = {t for t, r in by_turn.items() if len(r.get("receipt_refs", [])) >= r.get("runs", 0)}
    # Turns before from_turn were sent by a run whose receipts are lost; the skip is kept for every rerun.
    skipped_before = max(from_turn or 0, prev.get("skipped_before") or 0) or None
    pending = None

    def save():
        summary = {"world": world, "day": day, "vault": vault.origin, "conversation_ref": conv,
                   "speakers": speakers, "pushed_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                   "turns": len(receipts), "messages": sum(r["messages"] for r in receipts),
                   "skipped_before": skipped_before, "receipts": receipts, "history": history}
        tmp = out_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(summary, indent=1) + "\n")
        os.replace(tmp, out_path)
        return summary

    def ts(r):
        return int(dt.datetime.fromisoformat(r["ts"]).timestamp())

    for rec in records:
        if rec["role"] == "user":
            if pending is not None:
                print(f"  turn {pending['turn']}: user message without an answer, not witnessed", file=sys.stderr)
            pending = rec
            continue
        user, pending = (pending if pending is not None and pending["turn"] == rec["turn"] else None), None
        if rec["turn"] in done or (skipped_before is not None and rec["turn"] < skipped_before):
            continue
        if rec.get("witnessed_live") == conv:
            print(f"  turn {rec['turn']}: witnessed live by the day agent, skipped", file=sys.stderr)
            continue
        msgs = [("user", user["content"])] if user else []
        for tc in rec.get("tool_calls") or []:
            r = tc.get("result") or {}
            # only notes that were stored: a repeated fact returns "saved" with nothing written
            if tc["name"] == "remember" and r.get("saved") in (rec.get("notes_written") or []):
                was = f" (replaces {r['replaced']['old_value']})" if r.get("replaced") else ""
                msgs.append(("assistant", f"Note saved: {r['key']}: {r['value']}{was}"))
        if rec.get("content"):
            msgs.append(("assistant", rec["content"]))
        if not msgs:
            print(f"  turn {rec['turn']}: nothing to witness (error: {rec.get('error')})", file=sys.stderr)
            continue
        runs = speaker_runs(msgs)
        receipt = by_turn.get(rec["turn"])
        if receipt is not None and (receipt["messages"], receipt.get("runs")) != (len(msgs), len(runs)):
            raise SystemExit(f"turn {rec['turn']}: {receipt['messages']} messages were partly witnessed, the log now "
                             f"gives {len(msgs)}; finish it by hand")
        if receipt is None:
            receipt = {"turn": rec["turn"], "messages": len(msgs), "runs": len(runs), "receipt_refs": [],
                       "turn_short_ids": []}
            receipts.append(receipt)
            by_turn[rec["turn"]] = receipt
        for i, (author, texts) in enumerate(runs):
            if i < len(receipt["receipt_refs"]):
                continue  # this run reached the vault on an earlier try
            at = ts(user) if author == "user" else ts(rec)
            ids = [stable_id(f"{conv}:turn{rec['turn']}:run{i}:msg{j}") for j in range(len(texts))]
            try:
                out = vault.client_for(author).witness_run(conv, author, texts, at, ids)
            except VaultError as e:
                # A run whose call failed after the vault committed it (its receipt never came back): the resend's
                # stable ids are already in the vault under that earlier turn, so the run is witnessed.
                if e.status != 400 or "already belongs to another turn" not in e.body:
                    raise
                print(f"  turn {rec['turn']} run {i}: already in the vault (committed before an earlier failure)",
                      file=sys.stderr)
                out = {"receipt_ref": "already-witnessed", "turn_short_id": None}
            receipt["receipt_refs"].append(out.get("receipt_ref"))
            receipt["turn_short_ids"].append(out.get("turn_short_id"))
            save()  # after every run, so a failure in the next one never repeats this one
        print(f"  turn {rec['turn']}: {len(msgs)} messages in {len(runs)} runs witnessed", file=sys.stderr, flush=True)
        pending = None
    return save()


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Oneiron vault tools for Afterglow")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("push-day", help="witness a recorded day into the vault (for a Dreamer pass)")
    p.add_argument("--world", required=True)
    p.add_argument("--day", type=int, required=True)
    p.add_argument("--from-turn", type=int, help="skip earlier turns (they are already in the vault)")
    p.add_argument("--person-credential", help="the user's PERSON-bound credential file: user runs are witnessed "
                                               "with it, companion runs with AFTERGLOW_ONEIRON_CREDENTIAL")
    q = sub.add_parser("recall", help="ask the vault")
    q.add_argument("query")
    a = ap.parse_args()
    if a.cmd == "push-day":
        s = push_day(a.world, a.day, from_turn=a.from_turn, person_credential=a.person_credential)
        print(json.dumps({k: v for k, v in s.items() if k not in ("receipts",)}, indent=1))
    else:
        print(json.dumps(OneironVault().recall(a.query, limit=5), indent=1)[:4000])
