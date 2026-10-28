#!/usr/bin/env python3
"""What the product does with a question it cannot know: the SimpleQA gate slice asked through the day agent itself
(its real system prompt, the day context with the user's notes, the tool loop, reasoning "low", greedy), base and
each world's adapter, on the live server, one request at a time on slot 1.

The gate's prompt line "If you do not know it, say that you do not know" is not added: the user asks the bare
question. Each question runs in a fresh session under a scratch runs root (AFTERGLOW_RUNS), seeded with the serving
manifest and the world's latest notes, so the real day records are never touched. Scored with the night's abstain
rule and exact-match grade (eval/simpleqa_gate.py from --eval-root). Writes <out>/<arm>.jsonl (every answer) and
<out>/summary.json.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "night"))
from paths import runs_root  # noqa: E402


def seed_scratch(scratch, real_runs, world):
    """A runs root holding only the serving manifest and the world's latest notes (as day 0)."""
    (scratch / "serving").mkdir(parents=True, exist_ok=True)
    shutil.copy(real_runs / "serving" / "adapters.json", scratch / "serving" / "adapters.json")
    days = sorted(int(p.name) for p in (real_runs / world).glob("*") if p.name.isdigit() and (p / "notes.json").exists())
    if days:
        (scratch / world / "0").mkdir(parents=True, exist_ok=True)
        shutil.copy(real_runs / world / str(days[-1]) / "notes.json", scratch / world / "0" / "notes.json")
    return days[-1] if days else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-root", required=True, help="repo root whose eval/ holds the night's v3 harness")
    ap.add_argument("--endpoint", default="http://100.88.57.107:18610")
    ap.add_argument("--arm", action="append", required=True,
                    help="LABEL=WORLD:LORA_SCALE, e.g. base=w1-ines-varga:0 or w2-d2=w2-solo-developer:1")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--date", default="2026-10-30")
    ap.add_argument("--runs", help="the real runs root (default: the main checkout's runs/)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--deadline-s", type=float, default=3600)
    args = ap.parse_args()
    t0 = time.time()
    sys.path.insert(0, str(Path(args.eval_root) / "eval"))
    import aa_omniscience
    import simpleqa_gate

    real_runs = runs_root(args.runs)
    sys.path.insert(0, str(REPO / "app"))
    questions = simpleqa_gate.gate_items()[:args.limit]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    summary = {"endpoint": args.endpoint, "date": args.date, "n": len(questions), "arms": {}}
    for item in args.arm:
        label, spec = item.split("=", 1)
        world, scale = spec.split(":")
        rows, notes_day = [], None
        with tempfile.TemporaryDirectory(prefix=f"product-abst-{label}-") as tmp:
            for q in questions:
                if time.time() - t0 > args.deadline_s:
                    summary["stopped"] = f"deadline in arm {label}"
                    break
                scratch = Path(tmp) / q["id"]
                notes_day = seed_scratch(scratch, real_runs, world)
                os.environ["AFTERGLOW_RUNS"] = str(scratch)
                from afterglow.agent import DaySession  # paths are read per call, so the scratch root applies
                from afterglow.llm import GlimmerClient
                client = GlimmerClient(args.endpoint, slot=1, lora_scale=float(scale))
                session = DaySession(world, 1, client, date=args.date, reasoning="low", temperature=0.0)
                ev = session.ask(q["question"])
                answer = ev.get("content") or ""
                rows.append({"id": q["id"], "answer": answer, "abstain": bool(simpleqa_gate.is_abstain(answer)),
                             "grade": aa_omniscience.deterministic_grade(answer, q["answer"]), "error": ev.get("error"),
                             "total_s": ev.get("total_s"), "notes_written": len(ev.get("notes_written") or [])})
        os.environ.pop("AFTERGLOW_RUNS", None)
        (out / f"{label}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        n = len(rows)
        summary["arms"][label] = {"world": world, "lora_scale": float(scale), "notes_from_day": notes_day, "n": n,
                                  "abstain_rate": round(sum(r["abstain"] for r in rows) / n, 4) if n else None,
                                  "exact_correct": sum(r["grade"] == "correct" for r in rows),
                                  "errors": sum(bool(r["error"]) for r in rows),
                                  "notes_written": sum(r["notes_written"] for r in rows)}
        (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
        print(json.dumps({label: summary["arms"][label]}), flush=True)
    summary["elapsed_s"] = round(time.time() - t0, 1)
    (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")


if __name__ == "__main__":
    main()
