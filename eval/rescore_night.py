#!/usr/bin/env python3
"""Re-score a finished night from its saved answers with the current scorer (no model calls).

For each arm's score file in runs/<w>/<d>/night/eval/, every item is scored again from its saved answer
and truncation flag. The previous verdict stays in the file as "pass_<old version>", so nothing is lost.
eval.json and decision.json are rebuilt; the receipt gets a "rescore" entry with the old and new
headline numbers and every changed verdict. A changed decision is reported, not hidden.
"""

import argparse
import datetime
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT / "night"))
import scoring  # noqa: E402
from build_eval_json import build  # noqa: E402
from paths import runs_root  # noqa: E402
from run_night import decide  # noqa: E402
from score_peft import summarize  # noqa: E402


def headline(e):
    return {"facts_learned": e["learned"]["facts_learned"],
            "B_minus_A": e["learned"].get("B_minus_A"), "B_minus_C": e["learned"].get("B_minus_C"),
            "forgot": {k: e["forgot"][k] for k in ("delta", "ci95")} if e.get("forgot") else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", required=True)
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--runs")
    ap.add_argument("--old-version", default="v1", help="label for the verdicts being replaced")
    args = ap.parse_args()
    night = runs_root(args.runs) / args.world / str(args.day) / "night"
    world = json.loads((ROOT / "worlds" / args.world / "world.json").read_text())
    facts_by_id = {f["id"]: f for f in world["facts"]}
    old_eval = json.loads((night / "eval.json").read_text())
    old_decision = json.loads((night / "decision.json").read_text())
    probes = {json.loads(line)["id"]: json.loads(line) for line in (ROOT / "worlds" / args.world / "heldout" / "probes.jsonl").read_text().splitlines() if line.strip()}
    general = {}
    for name in ("general_slice_v1.jsonl", "forget_slice_v1.jsonl"):
        for line in (ROOT / "eval" / "data" / name).read_text().splitlines():
            if line.strip():
                item = json.loads(line)
                general[item["id"]] = {**item, "kind": item.get("kind", "general") if name.startswith("forget") else "general"}
    recs, changed = {}, []
    for arm in "ABCD":
        path = night / "eval" / f"score-night-{arm}.json"
        rec = json.loads(path.read_text())
        for r in rec["results"]:
            item = probes.get(r["id"]) or general[r["id"]]
            new, note = scoring.score_probe(item, r["answer"], r["truncated"], facts_by_id)
            old_key = f"pass_{args.old_version}"
            r.setdefault(old_key, r["pass"])
            if new != r[old_key]:
                changed.append({"arm": arm, "id": r["id"], "old": r[old_key], "new": new, "answer": r["answer"][:200]})
            r["pass"], r["note"] = new, note
        rec["summary"] = summarize(rec["results"], world)
        rec["scorer"] = scoring.SCORER_VERSION
        path.write_text(json.dumps(rec, indent=1) + "\n")
        recs[arm] = rec
    new_eval = build(world, args.day, recs, old_eval["adapter"])
    (night / "eval.json").write_text(json.dumps(new_eval, indent=1) + "\n")
    receipt = json.loads((night / "receipt.json").read_text())
    verify = json.loads((night / "gguf-verify.json").read_text())
    new_decision = decide(new_eval, verify, receipt.get("leak_check", {}).get("clean_passed", False))
    (night / "decision.json").write_text(json.dumps(new_decision, indent=1) + "\n")
    code = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    receipt.setdefault("rescore", []).append({
        "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "scorer": scoring.SCORER_VERSION,
        "replaces": args.old_version, "code_commit": code, "changed_verdicts": changed,
        "before": {**headline(old_eval), "decision": old_decision["decision"]},
        "after": {**headline(new_eval), "decision": new_decision["decision"]}})
    receipt.setdefault("hashes", {})["eval"] = hashlib.sha256((night / "eval.json").read_bytes()).hexdigest()
    receipt["hashes"]["decision"] = hashlib.sha256((night / "decision.json").read_bytes()).hexdigest()
    receipt["decision"] = new_decision["decision"]
    (night / "receipt.json").write_text(json.dumps(receipt, indent=1) + "\n")
    print(json.dumps({"world": args.world, "day": args.day, "changed": len(changed),
                      "facts_learned": [headline(old_eval)["facts_learned"], headline(new_eval)["facts_learned"]],
                      "decision": [old_decision["decision"], new_decision["decision"]]}))


if __name__ == "__main__":
    main()
