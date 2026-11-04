"""The open day's loom: streamed edits, reply alternatives, and the user's answer for the night.

Nothing runs in the background. Reading the loom never calls the model or changes the day log.
loom-resolution.json is an answer for the night runner to consume, not a training job.
"""
import json
from contextlib import contextmanager
from types import SimpleNamespace

from . import loom, paths
from .agent import now_iso
from .routes import Stream

# A night can create its folder for settings before it runs. Actual outputs seal the day,
# including receipt.json, which run_night.py writes when it starts.
NIGHT_OUTPUTS = ("receipt.json", "consolidated.json", "train.jsonl", "eval.json", "decision.json")


@contextmanager
def _idle(app):
    if not app.lock.acquire(blocking=False):
        raise RuntimeError("Afterglow is still answering the previous message")
    try:
        yield app.session
    finally:
        app.lock.release()


def _sealed(session):
    return any((night / name).exists() for night in session.dir.glob("night*")
               if night.is_dir() for name in NIGHT_OUTPUTS)


def _can_fork(session):
    if _sealed(session):
        raise RuntimeError("This day is sealed because its night has run. You cannot fork it.")


def _body(req):
    if not isinstance(req.body, dict):
        raise ValueError("Send a JSON object")
    return req.body


def _turn(body, key):
    turn = body.get(key)
    if isinstance(turn, bool) or not isinstance(turn, int) or turn < 1:
        raise ValueError(f"{key} must be a positive whole number")
    return turn


def _target(session, turn):
    target = next((r for r in session.history() if r.get("role") == "user" and r.get("turn") == turn), None)
    if target is None:
        raise ValueError(f"No user turn {turn} on your current branch")
    return target


def _branch_stream(app, session, branch, turn, text):
    # Stream(lock=True) acquires the chat lock before this iterator executes. The handler's
    # preflight returns real HTTP errors; recheck here in case another request changed the day.
    try:
        if app.session is not session or session.branch != branch:
            raise RuntimeError("Your current branch changed. Reload it and try again.")
        _can_fork(session)
        _target(session, turn)
    except (ValueError, RuntimeError) as e:
        yield {"type": "error", "message": str(e)}
        return
    session.fork(turn)
    yield from session.run_turn(text)


def branch(app, req):
    body = _body(req)
    turn = _turn(body, "at_turn")
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Your message cannot be empty")
    with _idle(app) as session:
        _can_fork(session)
        _target(session, turn)
        return Stream(_branch_stream(app, session, session.branch, turn, text), lock=True)


def regenerate(app, req):
    turn = _turn(_body(req), "turn")
    with _idle(app) as session:
        _can_fork(session)
        text = _target(session, turn)["content"]
        return Stream(_branch_stream(app, session, session.branch, turn, text), lock=True)


def switch(app, req):
    name = _body(req).get("branch")
    if not isinstance(name, str):
        raise ValueError("Choose a branch")
    with _idle(app) as session:
        session.switch(name)
        return session.loom_state()


def _facts(session, records):
    """Compare facts only when a side path adds or changes user words, never reply prose.

    A regenerated path with the same user words (or just a shorter prefix) adds no evidence.
    loom.line_records includes the shared prefix once, retaining note identity for contradictions.
    """
    branches = [loom.MAIN] + list(loom.forks(records))
    words = {name: tuple(r["content"] for r in loom.line_records(records, name) if r["role"] == "user")
             for name in branches}
    current = words[session.branch]
    changed, seen = [], set()
    for name, text in words.items():
        if name != session.branch and text != current[:len(text)] and text not in seen:
            changed.append(name)
            seen.add(text)
    on_line = {id(r) for r in loom.line_records(records, session.branch)}
    line_ids = {nid for r in records if id(r) in on_line for nid in r.get("notes_written") or []}
    filtered = [r for r in records if r.get("role") != "assistant" or id(r) in on_line
                or loom.branch_of(r) in changed]
    # remember() reuses a note for an unchanged fact, so a same-text regeneration may
    # write no note ids. Its earlier facts remain baseline, not side-branch evidence.
    # Prefer facts explicitly written on the current path over that baseline.
    notes = session.notes
    notes = [n for n in notes if n["id"] not in line_ids] + [n for n in notes if n["id"] in line_ids]
    return loom.contradictions(filtered, notes), changed


def _view(session, day=None):
    """Read a loom without opening a DaySession, seeding notes, or restoring the model."""
    if day is None or day == session.day:
        return SimpleNamespace(world=session.world, day=session.day, dir=session.dir,
                               branch=session.branch, turn=session.turn, records=session._records(),
                               notes=session.notes.all(include_superseded=True))
    if day >= session.day:
        raise ValueError("Choose an earlier recorded day")
    folder = paths.runs_root() / session.world / str(day)
    log = folder / "daylog.jsonl"
    if not (folder / "session.json").is_file():
        raise ValueError(f"No recorded day {day} for your open person")
    records = [json.loads(line) for line in log.read_text().splitlines() if line.strip()] if log.exists() else []
    notes_file = folder / "notes.json"
    notes = json.loads(notes_file.read_text())["notes"] if notes_file.exists() else []
    return SimpleNamespace(world=session.world, day=day, dir=folder, records=records,
                           branch=loom.current_line(records),
                           turn=max((r.get("turn", 0) for r in records if r.get("role") == "user"), default=0),
                           notes=notes)


def _resolution(session):
    file = session.dir / "loom-resolution.json"
    doc = json.loads(file.read_text()) if file.exists() else None
    if doc and (doc.get("line") != session.branch or doc.get("turn") != session.turn):
        return None  # the answer was for an earlier version of the day
    return doc


def state(app, req):
    value = req.query.get("day")
    day = None
    if value is not None:
        if not value.isascii() or not value.isdigit() or int(value) < 1:
            raise ValueError("day must be a positive whole number")
        day = int(value)
    with _idle(app) as session:
        view = _view(session, day)
        facts, changed = _facts(view, view.records)
        resolution = _resolution(view)
        sealed = _sealed(view)
        added = (resolution or {}).get("add_branches", [])
        fk = loom.forks(view.records)
        return {"line": view.branch, "branches": [loom.MAIN] + list(fk), "forks": fk,
                "alternatives": loom.alternatives(view.records, view.branch),
                "contradictions": facts, "sealed": sealed,
                "resolution": resolution, "side_branches": changed,
                "left_out": [b for b in changed if b not in added] if sealed else []}


def _choices(body, key):
    choices = body.get(key, [])
    if not isinstance(choices, list) or any(not isinstance(x, str) for x in choices):
        raise ValueError(f"{key} must be a list of names")
    return list(dict.fromkeys(choices))


def resolve(app, req):
    body = _body(req)
    keep, add = _choices(body, "keep_side"), _choices(body, "add_branches")
    day = _turn(body, "day") if "day" in body else None
    with _idle(app) as current:
        session = _view(current, day)
        facts, changed = _facts(session, session.records)
        side_ids = {f["side"]["id"] for f in facts["conflicts"]} | {f["id"] for f in facts["extra"]}
        if set(keep) - side_ids:
            raise ValueError("Choose side facts from your current loom")
        if set(add) - set(changed):
            raise ValueError("Choose side branches from your current loom")
        doc = {"world": session.world, "day": session.day, "line": session.branch, "turn": session.turn,
               "keep_side": keep, "add_branches": add, "resolved_at": now_iso()}
        file = session.dir / "loom-resolution.json"
        tmp = file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
        tmp.replace(file)
        return doc


ROUTES = {
    ("POST", "/api/branch"): branch,
    ("POST", "/api/branch/regenerate"): regenerate,
    ("POST", "/api/branch/switch"): switch,
    ("GET", "/api/loom"): state,
    ("POST", "/api/loom/resolve"): resolve,
}
