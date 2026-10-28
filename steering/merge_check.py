#!/usr/bin/env python3
"""Put a behaviour check into a night's record: copy the block to runs/<world>/<day>/<night dir>/behaviour-check.json and
add it to that night's eval.json as the one key "behaviour_check" (atomic write; nothing else in eval.json changes).
Runs where the runs/ tree lives (the Mac's main checkout, via night/paths.py).
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "night"))
from paths import runs_root  # noqa: E402


def atomic_write(path, text):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="append", required=True,
                    help="WORLD:DAY[:NIGHT_DIR]=behaviour-check JSON (NIGHT_DIR default night, e.g. night-2b)")
    ap.add_argument("--runs", help="runs root (default: the main checkout's runs/)")
    ap.add_argument("--no-eval-json", action="store_true", help="only copy the file; leave eval.json alone")
    args = ap.parse_args()
    root = runs_root(args.runs)
    for item in args.check:
        key, src = item.split("=", 1)
        world, day, *rest = key.split(":")
        night = root / world / day / (rest[0] if rest else "night")
        doc = json.loads(Path(src).read_text())
        if "behaviour_check" not in doc:
            raise SystemExit(f"{src} has no behaviour_check block")
        night.mkdir(parents=True, exist_ok=True)
        atomic_write(night / "behaviour-check.json", json.dumps(doc, indent=1) + "\n")
        merged = False
        ev = night / "eval.json"
        if ev.exists() and not args.no_eval_json:
            record = json.loads(ev.read_text())
            record["behaviour_check"] = doc["behaviour_check"]
            atomic_write(ev, json.dumps(record, indent=1) + "\n")
            merged = True
        print(json.dumps({"night": str(night), "eval_json_merged": merged,
                          "labels": {r["axis"]: r["label"] for r in doc["behaviour_check"]["axes"]}}))


if __name__ == "__main__":
    main()
