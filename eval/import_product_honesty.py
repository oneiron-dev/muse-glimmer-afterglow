#!/usr/bin/env python3
"""Copy Meta steering's honesty read (steering/honesty/honesty-all.json: per arm, the AA-style honesty score on the 200
general questions in the day agent's own form, its parts, never-told, the known slices and, from 30 Oct 10:05, the
general ask-back count {base, adapter, limit, pass}) into each night's
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
          "night3b": (3, "night-3b"), "night2b": (2, "night"), "night2d": (2, "night-2d"), "night4": (4, "night"), "idkr": (3, "night-3-idkr"),
          "night4b": (4, "night-4b"), "night5": (5, "night"),
          "night5b": (5, "night-5b"), "night5c": (5, "night-5c"), "night5d": (5, "night-5d"),
          "night5c2": (5, "night-5c2"), "night6": (6, "night"), "night6e3": (6, "night-6e3"),
          "night6kl1": (6, "night-6kl1"), "night6cd": (6, "night-6cd"), "night6kl05": (6, "night-6kl05"),
          "night6kl2": (6, "night-6kl2"), "night6pub12": (6, "night-6pub12"), "night7": (7, "night")}  # w4 2b was promoted to night/
# nights promoted into night/ after their read: the arm follows its folder (w3 4b promoted 29 Oct 21:04, its plain
# night 4 kept as night-replaced-20261029-2104/)
PROMOTED = {("w3", "night4"): (4, "night-replaced-20261029-2104"), ("w3", "night4b"): (4, "night"),
            ("w3", "night5"): (5, "night-replaced-20261030-0749"), ("w3", "night5c"): (5, "night"),  # 5c promoted 30 Oct (honesty-3k)
            ("w4", "night5"): (5, "night-replaced-20261030-0746"), ("w4", "night5c"): (5, "night")}


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
        day, night_dir = PROMOTED.get((world, which), NIGHTS[which])
        out = runs / WORLDS[world] / str(day) / night_dir / "eval" / "product-honesty-night.json"
        if not out.parent.exists():
            print(json.dumps({"arm": name, "skipped": f"{out.parent} missing"}))
            continue
        rec = {**arm, "arm": name, "source": args.src, "score": doc.get("score"), "grader": doc.get("grader"),
               "bootstrap": doc.get("bootstrap")}
        if out.exists():  # a known or ask-back read imported on its own (the served-set read) stays when this source lacks it
            old = json.loads(out.read_text())
            rec.update({k: old[k] for k in ("known_slice", "known_slice_v2", "ask_back") if old.get(k) and not rec.get(k)})
        out.write_text(json.dumps(rec, indent=1) + "\n")
        print(json.dumps({"arm": name, "written": str(out)}))


if __name__ == "__main__":
    main()
