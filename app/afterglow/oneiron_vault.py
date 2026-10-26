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
                                       "origin": ...})
  AFTERGLOW_CF_ACCESS_CLIENT_ID / _SECRET   the Access service token (hosted vaults)
Needs the "oneiron" extra: uv run --extra oneiron ...
"""
import json
import os
import secrets
import time
import urllib.error
import urllib.request

USER_AGENT = "afterglow-app/0.1 (+https://github.com/oneiron-dev/muse-glimmer-afterglow)"


class VaultError(RuntimeError):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status = status


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

    def holder_proof(self):
        timestamp = int(time.time()) + self.skew
        nonce = secrets.token_hex(16)
        challenge = f"oneiron-request:{timestamp}:{nonce}".encode()
        transcript = (b"oneiron/slip-binding/v2\0" + self.slip_digest
                      + len(challenge).to_bytes(8, "big") + challenge)
        return json.dumps({"timestamp": timestamp, "nonce": nonce, "signature": self.key.sign(transcript).hex()},
                          separators=(",", ":"))

    def _post(self, path, body, timeout=60):
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT, "Authorization": self.bearer,
                   "x-oneiron-binding": self.holder_proof(), **self.access}
        req = urllib.request.Request(self.origin + path, data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text = r.read()
                return json.loads(text) if text else None
        except urllib.error.HTTPError as e:
            raise VaultError(e.code, e.read().decode(errors="replace")) from e

    def facade(self, verb, body):
        """POST one facade verb. A 401 on the first call probes the vault's proof clock once (its authority
        clock can trail the wall clock) and retries with the offset it finds."""
        try:
            return self._post(f"/v1/core/facade/{verb}", body)
        except VaultError as e:
            if e.status != 401 or self.skew:
                raise
        for step in range(1, 31):
            for skew in (-60 * step, 60 * step):
                self.skew = skew
                try:
                    return self._post(f"/v1/core/facade/{verb}", body)
                except VaultError as e:
                    if e.status != 401:
                        raise
        self.skew = 0
        raise VaultError(401, "no holder proof within 30 min of the wall clock was accepted")

    # --- the verbs the notes backend uses ---
    def witness(self, conversation_ref, messages, occurred_at=None):
        """messages: [(author, text)] in order; author "user" or "assistant" (the vault calls it "companion").
        A witnessed turn carries one speaker, so each consecutive run of one author is its own turn.
        Returns one result per run."""
        runs = []
        for author, text in messages:
            author = "companion" if author == "assistant" else author
            if runs and runs[-1][0] == author:
                runs[-1][1].append(text)
            else:
                runs.append((author, [text]))
        at = int(occurred_at or time.time())
        return [self.facade("vault.witness", {
            "conversation_ref": conversation_ref, "occurred_at": at,
            "messages": [{"author": author, "message_type": "text", "content": t, "is_visible": True, "order": i}
                         for i, t in enumerate(texts)]}) for author, texts in runs]

    def recall(self, query, limit=5, effort="light"):
        return self.facade("vault.recall", {"query": query, "effort": effort, "limit": limit})


def push_day(world, day, vault=None):
    """Witness a recorded day (runs/<world>/<day>/daylog.jsonl) into the vault: per turn, the user's message,
    one assistant message per saved note, and the answer. Writes runs/<world>/<day>/oneiron.json."""
    import datetime as dt

    from . import paths
    from .notes import conversation_ref

    vault = vault or OneironVault()
    d = paths.day_dir(world, day)
    records = [json.loads(line) for line in (d / "daylog.jsonl").read_text().splitlines() if line.strip()]
    conv = conversation_ref(world, day)
    receipts, pending = [], None
    for rec in records:
        if rec["role"] == "user":
            pending = rec
            continue
        msgs = [("user", pending["content"])] if pending else []
        for tc in rec.get("tool_calls") or []:
            if tc["name"] == "remember" and "saved" in (tc.get("result") or {}):
                r = tc["result"]
                was = f" (replaces {r['replaced']['old_value']})" if r.get("replaced") else ""
                msgs.append(("assistant", f"Note saved: {r['key']}: {r['value']}{was}"))
        if rec.get("content"):
            msgs.append(("assistant", rec["content"]))
        at = int(dt.datetime.fromisoformat(rec["ts"]).timestamp())
        outs = vault.witness(conv, msgs, occurred_at=at)
        receipts.append({"turn": rec["turn"], "messages": len(msgs),
                         "receipt_refs": [o.get("receipt_ref") for o in outs],
                         "turn_short_ids": [o.get("turn_short_id") for o in outs]})
        pending = None
    summary = {"world": world, "day": day, "vault": vault.origin, "conversation_ref": conv,
               "pushed_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
               "turns": len(receipts), "messages": sum(r["messages"] for r in receipts), "receipts": receipts}
    (d / "oneiron.json").write_text(json.dumps(summary, indent=1) + "\n")
    return summary


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Oneiron vault tools for Afterglow")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("push-day", help="witness a recorded day into the vault (for a Dreamer pass)")
    p.add_argument("--world", required=True)
    p.add_argument("--day", type=int, required=True)
    q = sub.add_parser("recall", help="ask the vault")
    q.add_argument("query")
    a = ap.parse_args()
    if a.cmd == "push-day":
        s = push_day(a.world, a.day)
        print(json.dumps({k: v for k, v in s.items() if k != "receipts"}, indent=1))
    else:
        print(json.dumps(OneironVault().recall(a.query, limit=5), indent=1)[:4000])
