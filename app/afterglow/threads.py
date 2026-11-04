"""Subthreads: the side conversations of a day (a dictation, an explainer, a computer-use run), kept apart from the day's
chat so the night never trains on them unless the person keeps one.

runs/<world>/<day>/threads/index.json  [{"id": "t1", "kind", "title", "ts", "at_turn", "meta": {...}, "kept": false}]
runs/<world>/<day>/threads/<id>.jsonl  one record per line: {"ts", "role": "user"|"assistant"|"event", "content", ...}
kind: "dictation" | "explain" | "computer" | "note". at_turn: the chat turn it sits after (shown in place there).
The day log (daylog.jsonl) is never written here.
"""
import json
import re
import threading
import time
from pathlib import Path

from . import paths

KINDS = ("dictation", "explain", "computer", "note")
_lock = threading.Lock()


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def folder(world, day):
    return paths.runs_root() / world / str(int(day)) / "threads"


def _index(world, day):
    f = folder(world, day) / "index.json"
    return json.loads(f.read_text()) if f.exists() else []


def _write_index(world, day, items):
    d = folder(world, day)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "index.json.tmp"
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1) + "\n")
    tmp.replace(d / "index.json")


def create(world, day, kind, title, at_turn=None, meta=None):
    """A new subthread; returns its index entry."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    with _lock:
        items = _index(world, day)
        n = max((int(i["id"][1:]) for i in items if re.fullmatch(r"t\d+", i["id"])), default=0) + 1
        entry = {"id": f"t{n}", "kind": kind, "title": str(title or kind)[:120], "ts": now_iso(),
                 "at_turn": at_turn, "meta": meta or {}, "kept": False}
        items.append(entry)
        _write_index(world, day, items)
    return entry


def _check_id(tid):
    if not re.fullmatch(r"t\d+", str(tid)):
        raise ValueError("no such thread")
    return str(tid)


def append(world, day, tid, record):
    tid = _check_id(tid)
    if not any(i["id"] == tid for i in _index(world, day)):
        raise ValueError("no such thread")
    rec = {"ts": now_iso(), **record}
    with _lock, open(folder(world, day) / f"{tid}.jsonl", "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def records(world, day, tid):
    f = folder(world, day) / f"{_check_id(tid)}.jsonl"
    return [json.loads(line) for line in f.read_text().splitlines() if line.strip()] if f.exists() else []


def listing(world, day):
    return _index(world, day)


def get(world, day, tid):
    tid = _check_id(tid)
    entry = next((i for i in _index(world, day) if i["id"] == tid), None)
    if not entry:
        raise ValueError("no such thread")
    return {**entry, "records": records(world, day, tid)}


def update(world, day, tid, **fields):
    """Change an entry's title, meta or kept flag."""
    tid = _check_id(tid)
    with _lock:
        items = _index(world, day)
        for i in items:
            if i["id"] == tid:
                for k in ("title", "meta", "kept"):
                    if k in fields:
                        i[k] = fields[k]
                _write_index(world, day, items)
                return i
    raise ValueError("no such thread")
