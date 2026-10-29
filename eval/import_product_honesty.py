#!/usr/bin/env python3
"""Copy Meta steering's honesty read (steering/honesty/honesty-all.json: per arm, the AA-style honesty score on the 200
general questions in the day agent's own form, its parts and never-told) into each night's
eval/product-honesty-night.json, where eval/redecide_night.py --rule honesty-3 reads it. Arms without a night folder
(the base-swing noise floor) are skipped.

  eval/import_product_honesty.py --from /Users/olety/Desktop/temp/meta-hack-20261014/steering/honesty/honesty-all.json
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "night"))
from paths import runs_root  # noqa: E402

WORLDS = {"w1": "w1-ines-varga", "w2": "w2-solo-developer", "w3": "w3-shop-owner", "w4": "w4-grad-student"}
NIGHTS = {"night3": (3, "night"), "idk": (3, "night-3-idk"), "idkc": (3, "night-3-idkc"), "idk2to1": (3, "night-3-idk2to1"),
          "night3b": (3, "night-3b"), "night2b": (2, "night"), "night2d": (2, "night-2d"), "night4": (4, "night")}  # w4 2b was promoted to night/


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", required=True)
    ap.add_argument("--runs")
    args = ap.parse_args()
    doc = json.loads(Path(args.src).read_text())
    runs = runs_root(args.runs)
    for name, arm in doc["arms"].items():
        world, _, which = name.partition("-")
        if world not in WORLDS or which not in NIGHTS:
            print(json.dumps({"arm": name, "skipped": "no night folder"}))
            continue
        day, night_dir = NIGHTS[which]
        out = runs / WORLDS[world] / str(day) / night_dir / "eval" / "product-honesty-night.json"
        if not out.parent.exists():
            print(json.dumps({"arm": name, "skipped": f"{out.parent} missing"}))
            continue
        rec = {**arm, "arm": name, "source": args.src, "score": doc.get("score"), "grader": doc.get("grader"),
               "bootstrap": doc.get("bootstrap")}
        out.write_text(json.dumps(rec, indent=1) + "\n")
        print(json.dumps({"arm": name, "written": str(out)}))


if __name__ == "__main__":
    main()
