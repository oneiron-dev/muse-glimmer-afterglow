#!/usr/bin/env python3
"""Copy Meta steering's route-off never-told control (the day agent with the served route and supersede decisions
OFF, same server run as its base) into a night's eval/never-told-route-off.json, where decide() shows it as a
disclosed line: how often the weights alone keep the never-told boundary, and how many answers attach a saved fact to
another person (a look-alike probe answered) or make up a value (another key answered). The served form (route ON)
stays the check of record.

  eval/import_route_off.py --world W --day N --answers .../product-answers-night4-routeoff/<world>/answers-night4.jsonl
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "night")); sys.path.insert(0, str(ROOT / "eval"))
from omniscience import ABSTAIN_RULE  # noqa: E402
from paths import runs_root  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", required=True)
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--night-dir", default="night")
    ap.add_argument("--answers", required=True)
    ap.add_argument("--runs")
    args = ap.parse_args()
    probes = {p["id"]: p for p in map(json.loads, (ROOT / "eval" / "data" / "never_told" / f"{args.world}.jsonl").read_text().splitlines())}
    rows = [json.loads(line) for line in Path(args.answers).read_text().splitlines() if line.strip()]
    if any(r.get("route_on") for r in rows):
        raise SystemExit("these answers were taken with the route on: not the route-off control")
    out = {"source": args.answers, "server_run": rows[0].get("server_run"), "route_on": False, "supersede_on": rows[0].get("supersede_on"),
           "abstain_rule": "eval/omniscience.py ABSTAIN_RULE", "arms": {}}
    for arm in ("base", "adapter"):
        nt = [r for r in rows if r["arm"] == arm and r["set"] == "never_told"]
        answered = [r for r in nt if not ABSTAIN_RULE.search(r.get("answer") or "")]
        out["arms"][arm] = {"n": len(nt), "abstain": len(nt) - len(answered),
                            "wrong_person": sum(probes[r["id"]]["type"] == "lookalike" for r in answered),
                            "made_up": sum(probes[r["id"]]["type"] != "lookalike" for r in answered),
                            "answered_ids": [r["id"] for r in answered]}
    path = runs_root(args.runs) / args.world / str(args.day) / args.night_dir / "eval" / "never-told-route-off.json"
    path.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({"written": str(path), **{a: {k: v for k, v in x.items() if k != "answered_ids"} for a, x in out["arms"].items()}}))


if __name__ == "__main__":
    main()
