#!/usr/bin/env python3
"""Answers in the product's own form, for eval/product_abstention.py: questions asked through the day agent itself
(its real system prompt, the day context with the world's notes, the tool loop so it can call recall, reasoning
"low", greedy), on the live server, one request at a time on slot 1.

Sets (frozen): general = the SimpleQA gate slice questions, asked bare (no "say that you do not know" line);
never_told = the world's never-told probes. Arms: base (every adapter at 0) and each named adapter of the world
from the serving manifest (R2 = its day-1 adapter, night2 = its day-2 adapter), at scale 1.
Each question runs in a fresh session under a scratch runs root (AFTERGLOW_RUNS) seeded with the serving manifest
and the world's notes from --notes-day, so no real day record is touched.

Writes <out>/<world>/answers-<adapter arm>.jsonl in the scorer's shape ({"arm": "base"|"adapter", "set", "id",
"answer", "truncated"}; the base rows are shared by every adapter arm's file) and <out>/<world>/progress.json.
Order: never_told before general, and within a set base then each adapter arm, so a deadline cuts general last.
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


def manifest_adapter(real_runs, world, day):
    doc = json.loads((real_runs / "serving" / "adapters.json").read_text())
    hits = [a for a in doc.get("adapters", []) if a.get("world") == world and a.get("day") is not None
            and int(a["day"]) == day]  # kept or rolled back: a loaded adapter is measured either way
    return hits[-1] if hits else None


def ask(question, world, notes_file, manifest, endpoint, adapter_path, scale, date, tmp, slot):
    """One fresh day-agent session; returns (answer, truncated, error)."""
    scratch = Path(tempfile.mkdtemp(dir=tmp))
    (scratch / "serving").mkdir(parents=True)
    shutil.copy(manifest, scratch / "serving" / "adapters.json")
    rule = manifest.parent / "serving-rule.json"
    if rule.exists():  # the live product's serving rule; the fixed arms below still set their scales explicitly
        shutil.copy(rule, scratch / "serving" / "serving-rule.json")
    if notes_file:
        (scratch / world / "0").mkdir(parents=True)
        shutil.copy(notes_file, scratch / world / "0" / "notes.json")
    os.environ["AFTERGLOW_RUNS"] = str(scratch)
    from afterglow.agent import DaySession  # paths are read per call, so the scratch root applies
    from afterglow.llm import GlimmerClient
    client = GlimmerClient(endpoint, slot=slot, lora_scale=scale)
    session = DaySession(world, 1, client, date=date, reasoning="low", temperature=0.0)
    if adapter_path:
        session.client.adapter_path = adapter_path
    ev = session.ask(question)
    rec = [json.loads(line) for line in (scratch / world / "1" / "daylog.jsonl").read_text().splitlines()
           if line.strip()][-1]
    rounds = rec.get("rounds") or [{}]
    truncated = rounds[-1].get("finish_reason") in ("length", "limit")
    shutil.rmtree(scratch, ignore_errors=True)
    return ev.get("content") or "", truncated, ev.get("error")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-root", default=str(REPO), help="repo root whose eval/ holds the frozen sets")
    ap.add_argument("--endpoint", default="http://100.88.57.107:18610")
    ap.add_argument("--world", action="append", required=True)
    ap.add_argument("--adapters", default="R2:1,night2:2", help="ARM:DAY list from the serving manifest")
    ap.add_argument("--general-adapters", default="night2", help="adapter arms that also answer the general set")
    ap.add_argument("--slot", type=int, default=1, help="server slot; run one process per slot (one call per slot)")
    ap.add_argument("--notes-day", type=int, default=2)
    ap.add_argument("--sets", default="never_told,general")
    ap.add_argument("--limit-general", type=int, default=100,
                    help="first N gate-slice questions in the frozen hash order (100 = a direction screen)")
    ap.add_argument("--date", default="2026-10-30")
    ap.add_argument("--runs", help="the real runs root (default: the main checkout's runs/)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--deadline-s", type=float, default=7200)
    args = ap.parse_args()
    t0 = time.time()
    sys.path.insert(0, str(Path(args.eval_root) / "eval"))
    sys.path.insert(0, str(REPO / "app"))
    import never_told
    import simpleqa_gate

    real_runs = runs_root(args.runs)
    manifest = real_runs / "serving" / "adapters.json"
    general = [{"id": q["id"], "question": q["question"]} for q in simpleqa_gate.gate_items()[:args.limit_general]]
    out_note = {"general_questions": len(general), "note": "a direction screen on the first questions of the frozen slice"
                if len(general) < 200 else "the full gate slice"}
    out = Path(args.out)
    tmp = tempfile.mkdtemp(prefix="product-answers-")
    plan = []
    for s in args.sets.split(","):
        for w in args.world:
            arms = [("base", None, 0.0)]
            for spec in args.adapters.split(","):
                name, day = spec.split(":")
                entry = manifest_adapter(real_runs, w, int(day))
                if entry:
                    arms.append((name, entry["path"], 1.0))
                else:
                    print(json.dumps({"world": w, "arm": name, "skipped": f"no day-{day} adapter in the manifest"}), flush=True)
            items = [{"id": it["id"], "question": it["question"]} for it in never_told.load(w)] if s == "never_told" else general
            if s == "general" and not any(a[0] in args.general_adapters.split(",") for a in arms):
                print(json.dumps({"world": w, "set": s, "skipped": "no adapter arm to compare with the base"}), flush=True)
                continue
            for arm in arms:
                if s == "general" and arm[0] != "base" and arm[0] not in args.general_adapters.split(","):
                    continue
                plan.append((w, s, arm, items))
    rows = {}
    stopped = None
    for w, s, (arm, path, scale), items in plan:
        notes_file = real_runs / w / str(args.notes_day) / "notes.json"
        notes_file = notes_file if notes_file.exists() else None
        for it in items:
            if time.time() - t0 > args.deadline_s:
                stopped = f"deadline in {w}/{s}/{arm}"
                break
            tq = time.time()
            answer, truncated, error = ask(it["question"], w, notes_file, manifest, args.endpoint, path, scale, args.date,
                                           tmp, args.slot)
            row = {"arm_name": arm, "set": s, "id": it["id"], "answer": answer, "truncated": truncated, "error": error,
                   "seconds": round(time.time() - tq, 2)}
            rows.setdefault(w, []).append(row)
            (out / w).mkdir(parents=True, exist_ok=True)
            with open(out / w / "raw.jsonl", "a") as f:  # every answer as it lands, so a cut run keeps its work
                f.write(json.dumps(row) + "\n")
        if stopped:
            break
        wdir = out / w
        wdir.mkdir(parents=True, exist_ok=True)
        done = rows.get(w, [])
        base_rows = [r for r in done if r["arm_name"] == "base"]
        for name in {r["arm_name"] for r in done} - {"base"}:
            mine = [r for r in done if r["arm_name"] == name]
            (wdir / f"answers-{name}.jsonl").write_text("".join(
                json.dumps({"arm": "base" if r["arm_name"] == "base" else "adapter", "set": r["set"], "id": r["id"],
                            "answer": r["answer"], "truncated": r["truncated"]}) + "\n" for r in base_rows + mine))
        (wdir / "progress.json").write_text(json.dumps({**out_note,
            "counts": {f"{a}/{st}": sum(1 for r in done if r["arm_name"] == a and r["set"] == st)
                       for a in {r["arm_name"] for r in done} for st in {r["set"] for r in done}},
            "errors": sum(bool(r["error"]) for r in done), "elapsed_s": round(time.time() - t0, 1), "stopped": stopped},
            indent=1) + "\n")
        print(json.dumps({"world": w, "set": s, "arm": arm, "n": len(items), "elapsed_s": round(time.time() - t0, 1)}), flush=True)
    shutil.rmtree(tmp, ignore_errors=True)
    os.environ.pop("AFTERGLOW_RUNS", None)
    print(json.dumps({"stopped": stopped, "elapsed_s": round(time.time() - t0, 1)}))


if __name__ == "__main__":
    main()
