#!/usr/bin/env python3
"""Play a world's day script through the real day agent, so every night run has a reproducible day.

  python3 app/simulate_day.py --world w1-ines-varga --day 1
  python3 app/simulate_day.py --world w1-ines-varga --day 2 --date 2026-10-28

Reads worlds/<world>/days/<day>/script.jsonl; writes runs/<world>/<day>/daylog.jsonl, notes.json,
world_state.json and simulation.json (turns, timings, and which script facts reached the notes).
Resumes: script turns already in the day log are skipped. Day N starts from day N-1's notes.
"""
import argparse
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from afterglow import paths  # noqa: E402
from afterglow.agent import DaySession, load_world  # noqa: E402
from afterglow.llm import GlimmerClient  # noqa: E402

FIRST_DATE = dt.date(2026, 10, 27)


def norm(text):
    t = str(text).lower().replace("\u2019", "'")
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)
    t = re.sub(r"\b0(\d:\d\d)\b", r"\1", t)
    return re.sub(r"\s+", " ", re.sub(r"[-_#]+", " ", t)).strip()


def in_notes(fact, notes_text):
    return any(norm(a) in notes_text for a in fact["accept"])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--world", required=True)
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--date", help="the day's date (default: 27 Oct 2026 + day - 1)")
    ap.add_argument("--endpoint", default=GlimmerClient().endpoint)
    ap.add_argument("--reasoning", default="low", choices=["minimal", "low", "medium", "high"])
    ap.add_argument("--temperature", type=float, default=0.0, help="0 = greedy, so a day replays the same")
    a = ap.parse_args()

    script_path = paths.worlds_root() / a.world / "days" / str(a.day) / "script.jsonl"
    script = [json.loads(line) for line in script_path.read_text().splitlines() if line.strip()]
    date = a.date or (FIRST_DATE + dt.timedelta(days=a.day - 1)).isoformat()
    session = DaySession(a.world, a.day, GlimmerClient(a.endpoint), date=date, reasoning=a.reasoning,
                         temperature=a.temperature)
    done = {r["content"] for r in session.history() if r["role"] == "user"}
    t0 = time.time()
    turns = []
    for item in script:
        if item["content"] in done:
            continue
        ev = session.ask(item["content"], source="script")
        turns.append({"turn": item["turn"], "facts": item.get("facts", []), "notes_written": ev["notes_written"],
                      "ttft_s": ev["ttft_s"], "total_s": ev["total_s"], "error": ev["error"]})
        print(f"turn {item['turn']:>2}: {len(ev['notes_written'])} notes, {ev['total_s']:.1f} s"
              + (f"  ERROR {ev['error']}" if ev["error"] else "") + f"  | {ev['content'][:90]}", flush=True)
    session.end_day()

    world = load_world(a.world)
    notes = session.notes.all()
    notes_text = norm(" ".join(f"{n['key']} {n['value']}" for n in notes))
    stated = {fid for item in script for fid in item.get("facts", [])}
    facts = [f for f in world.get("facts", []) if f["id"] in stated]
    captured = [f["id"] for f in facts if in_notes(f, notes_text)]
    summary = {"world": a.world, "day": a.day, "date": date, "finished": dt.datetime.now().astimezone().isoformat(),
               "script_turns": len(script), "turns_run_now": len(turns), "wall_s": round(time.time() - t0, 1),
               "notes_active": len(notes), "notes_total": len(session.notes.all(include_superseded=True)),
               "facts_stated": len(facts), "facts_in_notes": len(captured),
               "facts_missing_from_notes": sorted(set(f["id"] for f in facts) - set(captured)),
               "errors": [t for t in turns if t["error"]], "turns": turns}
    out = session.dir / "simulation.json"
    if out.exists() and not turns:
        prev = json.loads(out.read_text())
        summary["turns"] = prev.get("turns", [])
    out.write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "turns"}, indent=1))


if __name__ == "__main__":
    main()
