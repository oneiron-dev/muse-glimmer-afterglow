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


def day_line_text(path):
    """The text unknown_key_rows searches for daylog inputs: the line's records."""
    return "\n".join(json.dumps(r) for r in day_line_records(Path(path).parent, path))
