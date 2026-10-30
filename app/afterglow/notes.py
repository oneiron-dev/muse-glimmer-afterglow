"""Notes: what the user told the agent, kept outside the weights.

One interface, NotesStore, with two backends: local JSON (LocalJsonNotes) and an Oneiron vault written through to
the local file (OneironNotes).
A note: {"id", "key", "value", "status": "active"|"superseded", "created_at", "updated_at",
         "day", "turn", "supersedes": id|null, "superseded_by": id|null}.
remember() with a key that matches an active note (case and spacing ignored), or with replaces naming one,
supersedes it, so a correction leaves the old value on record, marked stale.
"""
import datetime as dt
import json
import os
import re
from pathlib import Path

JST = dt.timezone(dt.timedelta(hours=9))
WORD = re.compile(r"[a-z0-9]+")
STOP = {"the", "a", "an", "of", "to", "my", "is", "are", "what", "which", "who", "when", "where", "in", "on", "at",
        "for", "and", "or", "do", "does", "i", "me", "our", "we", "it", "its", "with", "by", "from", "that", "this"}


def now_iso():
    return dt.datetime.now(JST).isoformat(timespec="seconds")


def norm_key(key):
    return " ".join(WORD.findall(key.lower()))


def terms(text):
    return {w for w in WORD.findall(text.lower()) if w not in STOP}


MONTH = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
SHAPES = [("date", re.compile(rf"\b\d{{4}}-\d{{2}}-\d{{2}}\b|\b\d{{1,2}}(st|nd|rd|th)? {MONTH}\b|\b{MONTH} \d{{1,2}}\b", re.I)),
          ("time", re.compile(r"\b\d{1,2}:\d{2}\b")),
          ("money", re.compile(r"\d[\d,.]*\s*(euros?|eur|dollars?|usd|pounds?|gbp|yen)\b|[€$£¥]\s*\d", re.I)),
          ("place", re.compile(r"\b(room|hall|bay|building|floor|office|lab|unit|desk)\s+[\w.-]+", re.I)),
          ("name", re.compile(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b"))]


def value_shapes(value):
    """What kinds of value a text holds (date, time, money, place, name): a correction replaces a value of its kind."""
    return {name for name, rx in SHAPES if rx.search(value)}


class NotesStore:
    """remember / recall / all / export_day. Backends implement all four."""

    def remember(self, key, value, *, day=None, turn=None, replaces=None):
        raise NotImplementedError

    def recall(self, query, k=5):
        raise NotImplementedError

    def all(self, include_superseded=False):
        raise NotImplementedError

    def export_day(self, path, *, world, day):
        raise NotImplementedError


class LocalJsonNotes(NotesStore):
    """Notes in one JSON file, rewritten atomically on every change."""

    def __init__(self, path, seed_from=None):
        self.path = Path(path)
        if self.path.exists():
            self.notes = json.loads(self.path.read_text())["notes"]
        elif seed_from and Path(seed_from).exists():
            self.notes = json.loads(Path(seed_from).read_text())["notes"]
            self._save()
        else:
            self.notes = []

    def _save(self, extra=None):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        doc = {"schema": "afterglow.notes/1", "updated_at": now_iso(), **(extra or {}), "notes": self.notes}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False))
        os.replace(tmp, self.path)

    def _next_id(self):
        return f"N{len(self.notes) + 1:04d}"

    def remember(self, key, value, *, day=None, turn=None, replaces=None):
        key, value = key.strip(), value.strip()
        active = [n for n in reversed(self.notes) if n["status"] == "active"]
        old = next((n for n in active if norm_key(n["key"]) == norm_key(key)), None)
        if old is None and replaces:  # a correction saved under a new key names the note it changes
            old = next((n for n in active if norm_key(n["key"]) == norm_key(replaces)), None)
        if old and old["value"].strip().lower() == value.lower():
            target = replaces and next((n for n in active if n is not old and norm_key(n["key"]) == norm_key(replaces)), None)
            if not target:
                return {"note": old, "superseded": None, "unchanged": True}
            # the fact was saved first under a new key; this call names the note it corrects
            self.link(old, target)
            return {"note": old, "superseded": target, "unchanged": False}
        t = now_iso()
        note = {"id": self._next_id(), "key": key, "value": value, "status": "active", "created_at": t,
                "updated_at": t, "day": day, "turn": turn, "supersedes": old["id"] if old else None,
                "superseded_by": None}
        if old:
            old.update(status="superseded", superseded_by=note["id"], updated_at=t)
        self.notes.append(note)
        self._save()
        return {"note": note, "superseded": old, "unchanged": False}

    def recall(self, query, k=5):
        q = terms(query)
        scored = []
        for i, n in enumerate(self.notes):
            if n["status"] != "active":
                continue
            overlap = len(q & terms(n["key"] + " " + n["value"]))
            key_hit = 2 if q & terms(n["key"]) else 0
            if overlap:
                scored.append((overlap + key_hit, i, n))
        scored.sort(key=lambda s: (-s[0], -s[1]))
        return [n for _, _, n in scored[:k]]

    def link(self, note, old, decided=None):
        """Mark `old` superseded by `note` (a correction saved under a new key). `decided`: how the link was
        made when not by the agent ({"by", "p", "latency_s"}), kept on the new note for the day page."""
        t = now_iso()
        old.update(status="superseded", superseded_by=note["id"], updated_at=t)
        note.update(supersedes=old["id"], updated_at=t)
        if decided:
            note["decided"] = decided
        self._save()

    def record_decision(self, note, decided):
        """Keep a supersede decision that linked nothing on the new note (the day page says both notes stay)."""
        note["decided"] = decided
        self._save()

    def correction_candidates(self, note, k=5):
        """Earlier active notes that the new `note` may correct (shown to the agent, which decides): shared key
        words count most, then the new key's words in the old value, words the two values share (a correction
        often keeps part of the value: the time, "Room", "Hall"), and a value of the same kind (a date for a date)."""
        kt, vt, shapes = terms(note["key"]), terms(note["value"]), value_shapes(note["value"])
        scored = []
        for i, n in enumerate(self.notes):
            if n["status"] != "active" or n["id"] == note["id"] or n.get("day") == note.get("day"):
                continue
            score = (2 * len(kt & terms(n["key"])) + len(kt & terms(n["value"])) + len(vt & terms(n["value"]))
                     + len(shapes & value_shapes(n["value"])))
            if score >= 2:
                scored.append((score, i, n))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        return [n for _, _, n in scored[:k]]

    def all(self, include_superseded=False):
        return [n for n in self.notes if include_superseded or n["status"] == "active"]

    def export_day(self, path, *, world, day):
        if Path(path).resolve() != self.path.resolve():
            other = LocalJsonNotes.__new__(LocalJsonNotes)
            other.path, other.notes = Path(path), self.notes
            other._save({"world": world, "day": day, "exported_at": now_iso()})
        else:
            self._save({"world": world, "day": day, "exported_at": now_iso()})
        return {"path": str(path), "active": len(self.all()), "total": len(self.notes)}


def stable_id(tag):
    """A deterministic 32-hex vault entity id for `tag`, with the top bits set clear of the engine's reserved
    low ids. The same tag always gives the same id, so a resent write names the same entity."""
    import hashlib

    h = hashlib.sha256(tag.encode()).hexdigest()
    return format(int(h[:16], 16) | (1 << 62), "016x") + h[16:32]


def conversation_ref(world, day, version=2, tag=None):
    """The vault conversation that holds one (world, day). Version 1 was witnessed with one credential for
    every speaker; version 2 names the user by their PERSON-bound credential. A tag names a separate
    conversation: a measured rerun of a day the vault already holds."""
    return stable_id(f"afterglow:{world}:{day}" + (f":v{version}" if version > 1 else "") + (f":{tag}" if tag else ""))


def world_conversations(world, day, version=2, tag=None):
    """The conversations one day's recall may read: this world's earlier days and the day itself, in either version
    (a restart can change the credentials); a tagged rerun reads its own conversation instead of the day's. The
    agent credential reads every person in the vault, so recall keeps only items from these."""
    earlier = {conversation_ref(world, d, v) for d in range(1, int(day)) for v in (1, 2)}
    today = {conversation_ref(world, day, version, tag)} if tag else {conversation_ref(world, day, v) for v in (1, 2)}
    return earlier | today


# a stored note as turn_messages (oneiron_vault.py) witnesses it
NOTE_LINE = re.compile(r"Note saved: (.+?): (.+?)(?: \(replaces .*\))?", re.S)
VAULT_RECALL_LIMIT = 20  # items asked of the vault per recall; items of other worlds are dropped before the top k
COLD_START_MS = 20000  # a vault call this slow most likely waited for a cold start (~27 s, Engine 30 Oct 09:56)


class OneironNotes(NotesStore):
    """Notes in an Oneiron vault (bright-otter-4848), written through to a local JSON store.

    Where the vault is the source, and where the local copy is:
    - Writes: every note goes to the local file at once (remember), and to the vault with its turn when the turn
      ends (witness_turn: the user's message, one "Note saved" message per note, the answer; push_day's shape and
      message ids, so a later push_day sends nothing twice). One worker sends them in order, off the chat's path.
    - recall (the recall tool): the vault is the source. It is asked with deadline_ms 30000; only items from this
      world's conversations count (world_conversations), a value the local copy marks superseded is dropped, and
      notes saved today that the vault has not acknowledged yet are added from the local copy. A vault error, a
      partial pack (retrieval_meta.partial), or a vault that is waking or down answers that call from the local
      notes; it is logged.
    - The vault is up, waking or down (vault_state), and no turn waits for it to change. The session wakes it in the
      background when it opens (a cold start takes ~27 s), and again before a recall after IDLE_STOP_S quiet (Hypnos
      stops it after 900 s idle). A failed call marks it down: recalls then answer locally at once, witness runs are
      logged unsent (push_day sends them later), and a background wake retries with backoff until it answers.
    - The local copy is the source for everything that needs the full note list: all(), the day context, the
      supersede decision (correction_candidates, link, record_decision), the route decision, export_day and the UI.

    recalls: one entry per recall call (ms, outcome, items kept and dropped), for the day log. wakes: every wake call
    ({"ms", "outcome": awake|error}). errors: every vault error and fallback. Needs the "oneiron" extra and the
    credentials in oneiron_vault.py.
    """

    PROBE_BACKOFF_S = (15, 30, 60, 120, 300)  # between wake calls while the vault is down
    IDLE_STOP_S = 840  # Hypnos stops the engine after 900 s idle: past this, a recall wakes it instead of waiting
    RECALL_TIMEOUT_S = 35  # the client's wait for one recall: the 30 s deadline plus the trip

    def __init__(self, local, world, day, vault=None, tag=None, witness_log=None):
        import queue
        import threading

        from .oneiron_vault import OneironVault, SpeakerVaults, live_witness_sent

        self.local, self.world, self.day = local, world, day
        template = os.environ.get("AFTERGLOW_ONEIRON_PERSON_CREDENTIAL_TEMPLATE")
        if vault is None:
            vault = SpeakerVaults(template.format(world=world)) if template else OneironVault()
        self.vault = vault
        version = 2 if isinstance(vault, SpeakerVaults) else 1
        self.conversation = conversation_ref(world, day, version, tag)
        self.readable = world_conversations(world, day, version, tag)
        self.witness_log = Path(witness_log) if witness_log else None
        self.notes = local.notes
        self.errors = []
        self.recalls = []
        # ids of notes saved today whose turn the vault has not acknowledged yet (a note rides in run 1, after the
        # user's run 0); a restarted app starts from the live log, so a turn it never sent stays local-served
        sent = live_witness_sent(self.witness_log) if self.witness_log else {}
        self.unacknowledged = {n["id"] for n in local.notes if n.get("day") == day and 1 not in sent.get(n.get("turn"), ())}
        self.wakes = []
        self.vault_state = "waking"
        self._up = threading.Event()  # set while the vault is up
        self._lock = threading.Lock()
        self._probing = False
        self._last_ok = None  # time of the last vault call that answered
        self._start_probe("waking")
        self._queue = queue.Queue()
        threading.Thread(target=self._send_loop, daemon=True).start()

    def _start_probe(self, state):
        """Mark the vault waking or down and start the background wake, unless one is running."""
        import threading

        self._up.clear()
        with self._lock:
            self.vault_state = state
            if self._probing:
                return
            self._probing = True
        threading.Thread(target=self._probe, daemon=True).start()

    def _probe(self):
        """Wake calls until one answers (the first right away, then with PROBE_BACKOFF_S between): then up."""
        import time

        for attempt in range(10 ** 6):
            t0 = time.time()
            try:
                self.vault.wake()
                self.wakes.append({"ms": round((time.time() - t0) * 1000), "outcome": "awake"})
                with self._lock:
                    self.vault_state, self._probing, self._last_ok = "up", False, time.time()
                self._up.set()
                return
            except Exception as e:
                err = f"{type(e).__name__}: {e}"[:300]
                self.wakes.append({"ms": round((time.time() - t0) * 1000), "outcome": "error", "error": err})
                self.errors.append(f"vault wake failed: {err}")
                self.vault_state = "down"
            time.sleep(self.PROBE_BACKOFF_S[min(attempt, len(self.PROBE_BACKOFF_S) - 1)])

    def _send_loop(self):
        import time

        from .oneiron_vault import VaultError

        while True:
            job = self._queue.get()
            t0 = time.time()
            line = {"turn": job["turn"], "run": job["run"], "author": job["author"], "messages": len(job["texts"]),
                    "queued_s": round(t0 - job["queued_at"], 3)}
            try:
                if self.vault_state == "down":  # not sent; push_day sends it later with the same ids
                    raise RuntimeError("vault down: not sent")
                try:
                    out = self.vault.client_for(job["author"]).witness_run(self.conversation, job["author"],
                                                                           job["texts"], job["at"], job["ids"]) or {}
                except VaultError as e:
                    # committed before a timeout, so the resend's ids are already in the vault (as in push_day)
                    if e.status != 400 or "already belongs to another turn" not in e.body:
                        raise
                    out = {"receipt_ref": "already-witnessed"}
                self.unacknowledged -= set(job["notes"])
                line["receipt_ref"] = out.get("receipt_ref")
                self._last_ok = time.time()
            except Exception as e:  # the vault is a second copy; a failed write never loses the local note
                line["error"] = f"{type(e).__name__}: {e}"[:300]
                self.errors.append(f"vault witness turn {job['turn']} run {job['run']}: {line['error']}")
                if self.vault_state != "down":
                    self._start_probe("down")
            finally:
                line["ms"] = round((time.time() - t0) * 1000)
                if self.witness_log:
                    with open(self.witness_log, "a", encoding="utf-8") as f:
                        f.write(json.dumps(line, ensure_ascii=False) + "\n")
                self._queue.task_done()

    def flush(self, timeout=None):
        """Wait until every queued witness call has been sent or logged unsent (the end of a day, a test), at most
        `timeout` seconds. True when the queue is empty."""
        import time

        end = None if timeout is None else time.time() + timeout
        with self._queue.all_tasks_done:
            while self._queue.unfinished_tasks:
                left = None if end is None else end - time.time()
                if left is not None and left <= 0:
                    return False
                self._queue.all_tasks_done.wait(left)
        return True

    def witness_turn(self, user_rec, rec):
        """Queue one finished turn for the vault: user_rec and rec are the turn's day-log records. Returns the
        number of messages queued (0: nothing to witness)."""
        import datetime as dt
        import time

        from .oneiron_vault import run_message_ids, speaker_runs, turn_messages

        ts = lambda r: int(dt.datetime.fromisoformat(r["ts"]).timestamp())
        msgs = turn_messages(user_rec, rec)
        for i, (author, texts) in enumerate(speaker_runs(msgs)):
            self._queue.put({"turn": rec["turn"], "run": i, "author": author, "texts": texts,
                             "at": ts(user_rec) if author == "user" and user_rec else ts(rec),
                             "ids": run_message_ids(self.conversation, rec["turn"], i, len(texts)),
                             "notes": [] if author == "user" else list(rec.get("notes_written") or []),
                             "queued_at": time.time()})
        return len(msgs)

    def remember(self, key, value, *, day=None, turn=None, replaces=None):
        r = self.local.remember(key, value, day=day, turn=turn, replaces=replaces)
        if not r["unchanged"]:
            self.unacknowledged.add(r["note"]["id"])
        return r

    def correction_candidates(self, note, k=5):
        return self.local.correction_candidates(note, k)

    def link(self, note, old, decided=None):
        return self.local.link(note, old, decided)

    def record_decision(self, note, decided):
        return self.local.record_decision(note, decided)

    def vault_notes(self, items):
        """Recall items -> notes, this world's only: a TURN or MESSAGE item from one of its conversations gives one
        note per message it quotes ("Note saved: key: value" back to key and value). Items of other worlds and
        items with no conversation (claims, summaries) are dropped, as are values the local copy marks superseded
        while no active note holds them. Returns (notes, dropped counts)."""
        active = {n["value"] for n in self.local.notes if n["status"] == "active"}
        stale = {n["value"] for n in self.local.notes if n["status"] == "superseded"} - active
        out, seen, dropped = [], set(), {"other_world": 0, "no_conversation": 0, "superseded": 0}
        for item in items:
            conv = item.get("conversation_id")
            if conv not in self.readable:
                dropped["other_world" if conv else "no_conversation"] += 1
                continue
            quoted = item.get("cited_messages") or [{"short_id": item.get("short_id"),
                                                     "value_text": item.get("value_text")}]
            for m in quoted:
                text = (m.get("value_text") or "").strip()
                line = NOTE_LINE.fullmatch(text)
                key, value = (line.group(1), line.group(2)) if line else ("from the conversation", text)
                if not value or value in seen:
                    continue
                if value in stale:
                    dropped["superseded"] += 1
                    continue
                seen.add(value)
                out.append({"id": m.get("short_id"), "key": key, "value": value, "status": "active",
                            "updated_at": "", "source": "vault"})
        return out, dropped

    def recall(self, query, k=5):
        import time

        from .oneiron_vault import recall_partial

        local = self.local.recall(query, k)
        call = {"query": query, "k": k}
        if self.vault_state == "up" and self._last_ok and time.time() - self._last_ok > self.IDLE_STOP_S:
            self._start_probe("waking")  # quiet long enough for Hypnos to have stopped it: wake, do not wait
        state = self.vault_state
        if state != "up":  # waking (a cold start, ~27 s) or down: the turn does not wait for the vault
            call.update(outcome=f"fallback_{state}", values=[n["value"][:200] for n in local])
            self.errors.append(f"vault {state} for {query!r}; answered from local notes")
            self.recalls.append(call)
            return local
        t0 = time.time()
        try:
            reply = self.vault.recall(query, limit=VAULT_RECALL_LIMIT, timeout=self.RECALL_TIMEOUT_S) or {}
            call["ms"] = round((time.time() - t0) * 1000)
            self._last_ok = time.time()
            items = reply.get("items") or []
            call["items"] = len(items)
            if recall_partial(reply):  # not finished: what is missing is unknown, so the local notes answer
                call["outcome"] = "fallback_partial"
                self.errors.append(f"vault recall not finished (partial) for {query!r}; answered from local notes")
            else:
                found, call["dropped"] = self.vault_notes(items)
                # read-your-writes: today's notes still on their way to the vault come from the local copy
                fresh = [n for n in local if n["id"] in self.unacknowledged and n["value"] not in {f["value"] for f in found}]
                out = (fresh + found)[:k]
                call.update(outcome="vault", kept=len(found), unacknowledged=len(fresh),
                            values=[n["value"][:200] for n in out], local_values=[n["value"][:200] for n in local])
                self.recalls.append(call)
                return out
        except Exception as e:
            call.setdefault("ms", round((time.time() - t0) * 1000))
            call.update(outcome="fallback_error", error=f"{type(e).__name__}: {e}"[:300])
            self.errors.append(f"vault recall failed for {query!r}: {call['error']}; answered from local notes")
            self._start_probe("down")
        call["values"] = [n["value"][:200] for n in local]
        self.recalls.append(call)
        return local

    def all(self, include_superseded=False):
        return self.local.all(include_superseded)

    def export_day(self, path, *, world, day):
        return self.local.export_day(path, world=world, day=day)

    def summary(self):
        """The day's vault numbers (simulation.json): recall latency (every call that reached the vault, fallbacks
        too), outcomes, items dropped, witness sends from the live log, and the error count."""
        sent = []
        if self.witness_log and self.witness_log.exists():
            sent = [json.loads(x) for x in self.witness_log.read_text().splitlines() if x.strip()]
        outcomes = {}
        for c in self.recalls:
            outcomes[c["outcome"]] = outcomes.get(c["outcome"], 0) + 1
        dropped = {}
        for c in self.recalls:
            for k, v in (c.get("dropped") or {}).items():
                dropped[k] = dropped.get(k, 0) + v
        slow = [ms for ms in [w["ms"] for w in self.wakes] + [c.get("ms") for c in self.recalls] + [x["ms"] for x in sent]
                if ms is not None and ms >= COLD_START_MS]
        return {"conversation_ref": self.conversation, "vault_state": self.vault_state, "wakes": list(self.wakes),
                "calls_over_20s": len(slow),
                "recalls": len(self.recalls), "recall_outcomes": outcomes,
                "recall_ms": spread([c["ms"] for c in self.recalls if "ms" in c]), "recall_dropped": dropped,
                "recalls_with_unacknowledged": sum(1 for c in self.recalls if c.get("unacknowledged")),
                "witness_runs": len(sent), "witness_errors": sum(1 for x in sent if x.get("error")),
                "witness_ms": spread([x["ms"] for x in sent]), "witness_queued_s": spread([x["queued_s"] for x in sent]),
                "errors": len(self.errors)}


def spread(values):
    """{n, p50, p90, max} of a list of numbers (nearest rank), or {n: 0}."""
    v = sorted(values)
    if not v:
        return {"n": 0}
    rank = lambda q: v[max(0, -(-len(v) * q // 100) - 1)]
    return {"n": len(v), "p50": rank(50), "p90": rank(90), "max": v[-1]}
