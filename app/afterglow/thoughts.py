"""The thought log: what Glimmer reasoned at night, from the trace proxy's records on the serving box.

  python3 -m afterglow.thoughts fetch --date 2026-10-27     # copy that day's night records to runs/traces/

The proxy writes every chat call to /data/afterglow/traces/<YYYY-MM-DD>.jsonl on the serving box. The night's
work is every call that is not the day chat (slot 0): Oneiron's Dreamer (from its own host, with no slot) and
the night job (slot 1). fetch() filters and slims them on the serving box and writes runs/traces/<date>.jsonl.
"""
import json
import subprocess

from . import paths
from .llm import HOSTS, SERVE_HOST

TRACE_DIR = f"{HOSTS['root']}/traces"
# The Dreamer calls from bel (Oneiron's frozen hack vault host); everything else off slot 0 is the night job.
DREAMER_CLIENTS = {"100.85.65.53": "Dreamer"}

REMOTE_FILTER = r"""
import json, sys
for line in open(sys.argv[1]):
    r = json.loads(line)
    if r.get("id_slot") == 0:
        continue
    msgs = r.get("messages") or []
    system = next((m.get("content") or "" for m in msgs if m.get("role") == "system"), "")
    user = next((m.get("content") or "" for m in reversed(msgs) if m.get("role") == "user"), "")
    print(json.dumps({"request_time": r.get("request_time"), "client": r.get("client"), "id_slot": r.get("id_slot"),
                      "system": system[:600], "user": user[:2000], "reasoning": (r.get("reasoning_content") or "")[:6000],
                      "content": (r.get("content") or "")[:3000], "finish_reason": r.get("finish_reason"),
                      "predicted_n": (r.get("timings") or {}).get("predicted_n")}))
"""


def traces_dir():
    d = paths.runs_root() / "traces"
    d.mkdir(parents=True, exist_ok=True)
    return d


def fetch(date, host=SERVE_HOST):
    """Copy the night's records for one trace file (JST date) from the serving box. Returns the record count."""
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", host, f"python3 - {TRACE_DIR}/{date}.jsonl"],
                       input=REMOTE_FILTER, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"fetch {host}:{TRACE_DIR}/{date}.jsonl failed: {r.stderr.strip()[:300]}")
    out = traces_dir() / f"{date}.jsonl"
    out.write_text(r.stdout)
    return len(r.stdout.splitlines())


def strip_echo(reasoning, user):
    """Glimmer's reasoning often opens by restating the request; drop that first paragraph."""
    head, sep, rest = reasoning.partition("\n\n")
    if sep and (head.lstrip().startswith(("{", "[")) or (user and head.strip()[:40] == user.strip()[:40])):
        return rest.strip()
    return reasoning.strip()


def dates():
    return sorted((p.stem for p in traces_dir().glob("*.jsonl")), reverse=True)


def load(date):
    """The night's thoughts for the UI, oldest first."""
    f = traces_dir() / f"{date}.jsonl"
    if not f.exists():
        return None
    out = []
    for line in f.read_text().splitlines():
        r = json.loads(line)
        who = DREAMER_CLIENTS.get(r["client"]) or ("Night job" if r.get("id_slot") == 1 else "Other")
        task = (r["system"] or r["user"]).strip().split("\n")[0].split(". ")[0][:120]
        out.append({"time": r["request_time"], "who": who, "task": task,
                    "input": r["user"], "thought": strip_echo(r["reasoning"], r["user"]), "answer": r["content"],
                    "tokens": r.get("predicted_n")})
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Thought log tools")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--date", required=True, help="trace file date, YYYY-MM-DD (JST)")
    a = ap.parse_args()
    print(f"{fetch(a.date)} night records -> {traces_dir() / (a.date + '.jsonl')}")
