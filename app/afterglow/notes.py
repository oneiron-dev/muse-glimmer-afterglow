"""Notes: what the user told the agent, kept outside the weights.

One interface, NotesStore, with a local JSON backend now and an Oneiron backend later.
A note: {"id", "key", "value", "status": "active"|"superseded", "created_at", "updated_at",
         "day", "turn", "supersedes": id|null, "superseded_by": id|null}.
remember() with a key that matches an active note (case and spacing ignored) supersedes it,
so a correction leaves the old value on record, marked stale.
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


class NotesStore:
    """remember / recall / all / export_day. Backends implement all four."""

    def remember(self, key, value, *, day=None, turn=None):
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

    def remember(self, key, value, *, day=None, turn=None):
        key, value = key.strip(), value.strip()
        nk = norm_key(key)
        old = next((n for n in reversed(self.notes) if n["status"] == "active" and norm_key(n["key"]) == nk), None)
        if old and old["value"].strip().lower() == value.lower():
            return {"note": old, "superseded": None, "unchanged": True}
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


def conversation_ref(world, day, version=2):
    """The vault conversation that holds one (world, day). Version 1 was witnessed with one credential for
    every speaker; version 2 names the user by their PERSON-bound credential."""
    return stable_id(f"afterglow:{world}:{day}" + (f":v{version}" if version > 1 else ""))


class OneironNotes(NotesStore):
    """Notes in an Oneiron vault (bright-otter-4848), mirrored in a local JSON store.

    The vault is where the Dreamer consolidates the day: every saved note and every turn is witnessed into
    the day's conversation. The local mirror keeps notes.json for the UI and the night job. recall() asks the
    vault first and adds local matches. Needs the "oneiron" extra and the credentials in oneiron_vault.py.
    """

    def __init__(self, local, world, day, vault=None):
        import queue
        import threading

        import os

        from .oneiron_vault import OneironVault, SpeakerVaults

        self.local, self.world, self.day = local, world, day
        template = os.environ.get("AFTERGLOW_ONEIRON_PERSON_CREDENTIAL_TEMPLATE")
        if vault is None:
            vault = SpeakerVaults(template.format(world=world)) if template else OneironVault()
        self.vault = vault
        self.conversation = conversation_ref(world, day, 2 if isinstance(vault, SpeakerVaults) else 1)
        self.notes = local.notes
        self.errors = []
        # Witness calls take seconds on a busy vault; one worker sends them in order, off the chat's path.
        self._queue = queue.Queue()
        threading.Thread(target=self._send_loop, daemon=True).start()

    def _send_loop(self):
        while True:
            messages, at = self._queue.get()
            try:
                self.vault.witness(self.conversation, messages, occurred_at=at)
            except Exception as e:  # the vault is a second copy; a failed write never loses the local note
                self.errors.append(f"{type(e).__name__}: {e}")
            finally:
                self._queue.task_done()

    def _witness(self, messages):
        import time

        self._queue.put((messages, int(time.time())))

    def flush(self, timeout=None):
        """Wait until every queued witness call has been sent (the end of a day, a test)."""
        self._queue.join()

    def remember(self, key, value, *, day=None, turn=None):
        r = self.local.remember(key, value, day=day, turn=turn)
        if not r["unchanged"]:
            was = f" (replaces {r['superseded']['value']})" if r["superseded"] else ""
            self._witness([("assistant", f"Note saved: {key}: {value}{was}")])
        return r

    def recall(self, query, k=5):
        out = []
        try:
            items = (self.vault.recall(query, limit=k) or {}).get("items", [])
            out = [{"id": i.get("short_id"), "key": i.get("predicate") or i.get("kind", "").lower(),
                    "value": i.get("value_text") or "", "status": "active", "updated_at": "", "source": "vault"}
                   for i in items if i.get("value_text")]
        except Exception as e:
            self.errors.append(f"{type(e).__name__}: {e}")
        local = self.local.recall(query, k)
        return local + [n for n in out if n["value"] not in {m["value"] for m in local}][:max(0, k - len(local))]

    def all(self, include_superseded=False):
        return self.local.all(include_superseded)

    def export_day(self, path, *, world, day):
        return self.local.export_day(path, world=world, day=day)

    def witness_turn(self, user_text, assistant_text):
        return self._witness([("user", user_text), ("assistant", assistant_text)])
