"""The day's line for the night builder: loom.day_line over daylog.jsonl.

A day log with no forks gives every record, exactly as before. When the user
has picked a branch (runs/<w>/<d>/loom-resolution.json holding {"line"} or
{"branch"}), the named branch wins over the log's own current line.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from afterglow import loom  # noqa: E402

RESOLUTION_FILENAME = "loom-resolution.json"
DAYLOG_FILENAME = "daylog.jsonl"


def read_records(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def resolve_branch(run_dir, records):
    """The branch the night trains on: loom-resolution.json wins when it names a
    known branch, else the log's own current line."""
    res_path = Path(run_dir) / RESOLUTION_FILENAME
    if res_path.exists():
        try:
            doc = json.loads(res_path.read_text())
        except (json.JSONDecodeError, OSError):
            doc = {}
        name = doc.get("line", doc.get("branch")) if isinstance(doc, dict) else None
        if name == loom.MAIN or name in loom.forks(records):
            return name
    return loom.current_line(records)


def day_line_records(run_dir, path=None):
    """The daylog records the night trains on: the resolved line's records."""
    run_dir = Path(run_dir)
    records = read_records(path or run_dir / DAYLOG_FILENAME)
    return loom.line_records(records, resolve_branch(run_dir, records))


def day_line_notes(run_dir, notes):
    """The day's notes as the resolved line has them (loom.line_notes), without the notes said on other versions of
    the day. A day log without forks gives `notes` itself, unchanged."""
    run_dir = Path(run_dir)
    path = run_dir / DAYLOG_FILENAME
    if not path.exists():
        return notes
    records = read_records(path)
    if not loom.forks(records):
        return notes
    day = int(run_dir.name) if run_dir.name.isdigit() else None
    view = loom.line_notes(records, notes, day, resolve_branch(run_dir, records))
    return [n for n in view if n.get("status") != loom.OTHER_VERSION]


def day_line_text(path):
    """The text unknown_key_rows searches for daylog inputs: the records on
    the resolved line's path, with loom's own per-branch cut (a branch's turns
    at or after the fork that replaced them are off the line).

    Branchless file records (fork markers, night_links) ride along. Dialog
    records match loom.line_records exactly; an unforked log round-trips the
    raw file exactly (ensure_ascii=False, as agent.py writes it).
    """
    path = Path(path)
    records = read_records(path)
    branch = resolve_branch(path.parent, records)
    fk = loom.forks(records)
    chain = []
    b = branch
    while b != loom.MAIN and b in fk:
        chain.append(b)
        b = fk[b]["parent"]
    chain.append(loom.MAIN)
    chain.reverse()
    cuts = {b: (fk[chain[i + 1]]["at_turn"] if i + 1 < len(chain) else None) for i, b in enumerate(chain)}
    out = []
    for r in records:
        b = loom.branch_of(r)
        if b not in cuts:
            continue
        cut = cuts[b]
        if cut is not None and r.get("turn", 0) >= cut:
            continue
        out.append(r)
    return "\n".join(json.dumps(r, ensure_ascii=False) for r in out)
