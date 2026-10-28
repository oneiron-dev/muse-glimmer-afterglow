#!/usr/bin/env python3
"""Judge the day-axis screen rows: does the steered reply lean to the axis's positive pole, and is it still coherent?

For every day-axis row of a screen output and each arm, each steered reply is compared with the same arm's
unsteered reply to the same prompt (the baseline row). Muse Spark (development subscription, spark-sub teacher;
synthetic prompts only) answers which reply is more <positive pole>, and rates both replies' coherence 1-5.
Reply order is fixed per prompt by a hash, half the time steered first. Writes <screen>/judge-day.json.
Run with CLIPROXY_KEY set for the one command; the key is never logged.
"""

import argparse
import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "night"))
from teacher import complete  # noqa: E402

PROMPT = """Two assistant replies answer the same user message. Compare them on one property only.

Property: which reply is more {pos} (the opposite is {neg})?

User message:
{prompt}

Reply A:
{a}

Reply B:
{b}

Return only JSON: {{"more": "A" or "B" or "same", "coherent_A": 1-5, "coherent_B": 1-5}}.
Coherent 5 = fluent and on topic; 1 = broken, repetitive or off topic."""


def judge(axis, prompt, steered, base, item_id, log_path):
    steered_first = int(hashlib.sha256(item_id.encode()).hexdigest(), 16) % 2 == 0
    a, b = (steered, base) if steered_first else (base, steered)
    text, _, _ = complete("spark-sub", [{"role": "user", "content": PROMPT.format(
        pos=axis["positive_label"], neg=axis["negative_label"], prompt=prompt, a=a or "(empty)", b=b or "(empty)")}],
        max_tokens=400, log_path=log_path, purpose="steering day-axis judge")
    m = re.search(r"\{.*\}", text, re.S)
    v = json.loads(m.group(0)) if m else {}
    more = v.get("more", "same")
    steered_label = "A" if steered_first else "B"
    toward = 1.0 if more == steered_label else 0.5 if more == "same" else 0.0
    return {"toward_positive": toward,
            "coherent_steered": v.get(f"coherent_{steered_label}"),
            "coherent_base": v.get(f"coherent_{'B' if steered_first else 'A'}")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--screen", required=True, help="screen output dir with the day-axis rows")
    ap.add_argument("--baseline", help="the unsteered row JSON (default: <screen>/baseline.json)")
    ap.add_argument("--axes", default=str(HERE / "axes.json"))
    ap.add_argument("--parallel", type=int, default=6)
    args = ap.parse_args()
    screen = Path(args.screen)
    axes = {a["name"]: a for a in json.loads(Path(args.axes).read_text())["axes"]}
    base = json.loads(Path(args.baseline or screen / "baseline.json").read_text())
    jobs = []
    for f in sorted(screen.glob("*.json")):
        if f.name in ("baseline.json", "summary.json", "judge-day.json"):
            continue
        rec = json.loads(f.read_text())
        for arm, tests in rec.get("arms", {}).items():
            for test, out in tests.items():
                if not test.startswith("day:"):
                    continue
                name = test[4:]
                ref = {r["id"]: r for r in base["arms"][arm][test]["results"]}
                for i, r in enumerate(out["results"]):
                    jobs.append((rec["row"]["name"], arm, name, axes[name]["test_prompts"][i], r, ref[r["id"]]))
    log_path = screen / "judge-day.calls.jsonl"
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        verdicts = list(pool.map(lambda j: judge(axes[j[2]], j[3], j[4]["answer"], j[5]["answer"],
                                                 f"{j[0]}/{j[1]}/{j[4]['id']}", log_path), jobs))
    rows = {}
    for (row, arm, name, _, r, _), v in zip(jobs, verdicts):
        rows.setdefault(row, {}).setdefault(arm, []).append({**v, "id": r["id"]})
    summary = {}
    for row, arms in rows.items():
        summary[row] = {}
        for arm, vs in arms.items():
            cs = [v["coherent_steered"] for v in vs if isinstance(v["coherent_steered"], (int, float))]
            cb = [v["coherent_base"] for v in vs if isinstance(v["coherent_base"], (int, float))]
            summary[row][arm] = {"n": len(vs), "toward_positive": round(sum(v["toward_positive"] for v in vs) / len(vs), 3),
                                 "coherence_steered": round(sum(cs) / len(cs), 2) if cs else None,
                                 "coherence_base": round(sum(cb) / len(cb), 2) if cb else None}
    (screen / "judge-day.json").write_text(json.dumps({"judge": "muse-spark-1.3-contributor (spark-sub)",
                                                       "summary": summary, "rows": rows}, indent=1) + "\n")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
