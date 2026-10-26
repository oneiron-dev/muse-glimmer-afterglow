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


class OneironNotes(NotesStore):
    """Oneiron vault backend (bel vault bright-otter-4848). Not wired yet: the Meta lead says when the vault is
    ready after the owner's smoke sitting. Until then every call refuses, so nothing reaches the vault early."""

    def __init__(self, vault="bright-otter-4848"):
        self.vault = vault

    def _refuse(self, *a, **kw):
        raise NotImplementedError(f"Oneiron notes backend ({self.vault}) waits for the Meta lead's go")

    remember = recall = all = export_day = _refuse
