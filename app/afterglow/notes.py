"""Notes: what the user told the agent, kept outside the weights.

One interface, NotesStore, with a local JSON backend now and an Oneiron backend later.
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

    def apply_night_links(self, links, night_day):
        """The corrections a night found and its guard passed (runs/<w>/<d>/night/night-links.json "links"): each
        old note gets superseded_by_night {by, night_day, linked_by, p, guard, reason} and status superseded, so it
        leaves recall and the notes block; it stays on disk. A link whose notes are gone or no longer active, or
        that the user undid, is not applied. Returns the links applied."""
        by_id = {n["id"]: n for n in self.notes}
        applied = []
        for link in links:
            old, new = by_id.get(link.get("old_id")), by_id.get(link.get("new_id"))
            if not old or not new or old["status"] != "active" or new["status"] != "active":
                continue
            if any(u.get("by") == new["id"] for u in old.get("night_links_undone") or []):
                continue
            old.update(status="superseded", updated_at=now_iso(),
                       superseded_by_night={"by": new["id"], "night_day": night_day,
                                            **{k: link.get(k) for k in ("linked_by", "p", "guard", "reason")}})
            applied.append(link)
        if applied:
            self._save()
        return applied

    def undo_night_link(self, old_id, day=None):
        """The user's undo of a night's link: the old note is active again, and the undo stays on it
        (night_links_undone: by, night_day, at, day), so the next night sees it and no later start applies it again.
        Returns the link undone."""
        old = next((n for n in self.notes if n["id"] == old_id and n.get("superseded_by_night")), None)
        if old is None:
            raise KeyError(f"{old_id}: no night link to undo")
        link = old.pop("superseded_by_night")
        old.setdefault("night_links_undone", []).append({"by": link["by"], "night_day": link["night_day"],
                                                         "at": now_iso(), "day": day})
        old.update(status="active", updated_at=now_iso())
        self._save()
        return link

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

    def remember(self, key, value, *, day=None, turn=None, replaces=None):
        r = self.local.remember(key, value, day=day, turn=turn, replaces=replaces)
        if not r["unchanged"]:
            was = f" (replaces {r['superseded']['value']})" if r["superseded"] else ""
            self._witness([("assistant", f"Note saved: {key}: {value}{was}")])
        return r

    def correction_candidates(self, note, k=5):
        return self.local.correction_candidates(note, k)

    def link(self, note, old, decided=None):
        return self.local.link(note, old, decided)

    def record_decision(self, note, decided):
        return self.local.record_decision(note, decided)

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
