"""Earlier days, read only, for the one-thread chat and Memory > Days: a day's line of messages and a summary of every
recorded day of the open person. Reads runs/<world>/<day>/ (session.json, daylog.jsonl); never writes or opens a day."""
import json

from . import loom, paths

KEEP = ("ts", "turn", "role", "content", "typed", "attachments", "tool_calls", "error", "notes_written")


def _day_folder(world, day):
    return paths.runs_root() / world / str(int(day))


def _records(folder):
    f = folder / "daylog.jsonl"
    return [json.loads(line) for line in f.read_text().splitlines() if line.strip()] if f.exists() else []


def _date(folder):
    f = folder / "session.json"
    try:
        return json.loads(f.read_text()).get("date")
    except (OSError, ValueError):
        return None


def recorded_days(world):
    root = paths.runs_root() / world
    if not root.is_dir():
        return []
    return sorted(int(d.name) for d in root.iterdir() if d.name.isdigit() and (d / "session.json").exists())


def day_history(app, req):
    """GET /api/day/history?day=N: the day's line (what the night trained on), trimmed to what the thread shows."""
    world = app.session.world
    day = req.query.get("day", "")
    if not day.isdigit() or int(day) not in recorded_days(world):
        raise ValueError(f"no recorded day {day} for {world}")
    folder = _day_folder(world, day)
    line = loom.day_line(_records(folder))
    return {"world": world, "day": int(day), "date": _date(folder),
            "history": [{k: r[k] for k in KEEP if k in r} for r in line]}


def days_summary(app, req):
    """GET /api/days/summary: every recorded day of the open person, newest first: date, turns, the first thing said,
    and how many notes were written."""
    world = app.session.world
    out = []
    for d in recorded_days(world):
        folder = _day_folder(world, d)
        line = loom.day_line(_records(folder))
        users = [r for r in line if r.get("role") == "user"]
        first = next((r.get("typed") or r.get("content") or "" for r in users), "")
        notes = sum(len(r.get("notes_written") or []) for r in line if r.get("role") == "assistant")
        out.append({"day": d, "date": _date(folder), "turns": len(users), "first": first[:160], "notes": notes})
    return {"world": world, "days": list(reversed(out))}


ROUTES = {
    ("GET", "/api/day/history"): day_history,
    ("GET", "/api/days/summary"): days_summary,
}
