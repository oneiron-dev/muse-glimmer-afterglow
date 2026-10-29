#!/usr/bin/env python3
"""Make an experiment night the world's night of record for its day, after the owner's ruling.

  night/promote_night.py --world W --day N --from night-2b --rule product-2s|report-only|v3

runs/W/N/night (if any) moves to runs/W/N/night-replaced-<stamp>; runs/W/N/<from> becomes runs/W/N/night; the
adapter is recorded in runs/W/adapters.json "history" (day, gguf, sha, host_gguf, decision); then
eval/redecide_night.py --of-record re-decides it under the ruled rule, which moves "current" on a keep. Run only
on the owner's word (passed on by the Meta lead).
"""

import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "night"))
from paths import runs_root  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", required=True)
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--from", dest="source", required=True, help="the experiment folder, e.g. night-2b")
    ap.add_argument("--rule", required=True)
    ap.add_argument("--note", help="what the promoted night is, opening its decision line (kept in receipt.json \"promoted\")")
    ap.add_argument("--runs")
    args = ap.parse_args()
    runs = runs_root(args.runs)
    day_dir = runs / args.world / str(args.day)
    src, dst = day_dir / args.source, day_dir / "night"
    if not (src / "decision.json").exists():
        raise SystemExit(f"{src} has no decision.json")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    if dst.exists():
        dst.rename(day_dir / f"night-replaced-{stamp}")
    src.rename(dst)
    receipt = json.loads((dst / "receipt.json").read_text())
    receipt["promoted"] = {"from": args.source, "at": stamp, "rule": args.rule, "note": args.note,
                           "replaced": f"night-replaced-{stamp}" if (day_dir / f"night-replaced-{stamp}").exists() else None}
    (dst / "receipt.json").write_text(json.dumps(receipt, indent=1) + "\n")
    pointer_path = runs / args.world / "adapters.json"
    pointer = json.loads(pointer_path.read_text()) if pointer_path.exists() else {"current": None, "history": []}
    host = receipt.get("host", "mel")
    pointer["history"].append({"day": args.day, "gguf": str(dst / "adapter.gguf"), "gguf_sha256": receipt["hashes"]["adapter_gguf"],
                               "host_gguf": f"{host}:/data/meta-night/nights/{args.world}/{args.day}/{args.source}/adapter.gguf",
                               "decision": json.loads((dst / "decision.json").read_text())["decision"],
                               "promoted_from": args.source, "at": stamp})
    pointer_path.write_text(json.dumps(pointer, indent=1) + "\n")
    subprocess.run([sys.executable, str(ROOT / "eval" / "redecide_night.py"), "--world", args.world, "--day", str(args.day),
                    "--rule", args.rule, "--of-record", *(["--runs", args.runs] if args.runs else [])], check=True)


if __name__ == "__main__":
    main()
