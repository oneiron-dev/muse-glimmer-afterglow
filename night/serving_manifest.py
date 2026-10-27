#!/usr/bin/env python3
"""Stage the kept adapters on the serving box and write runs/serving/adapters.json for the day server.

For every world, the kept adapter is runs/<w>/adapters.json "current". Its GGUF is copied from the
training host to <serving dir>/<w>-d<day>.gguf (host to host, checked by sha256), and the manifest lists
the adapters with explicit ids (0..n-1, the server's --lora order). Entries without a "world" (adapters this
script does not own) are kept. Paths are absolute on the serving box and unique. Nothing here restarts a server.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import runs_root  # noqa: E402

LLAMA_SERVER = "/data/afterglow/llama-v0.7.0-620d222/bin/llama-server"


def ssh(host, cmd):
    return subprocess.run(["ssh", host, cmd], capture_output=True, text=True, check=True).stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--serving-host", default="nelly")
    ap.add_argument("--serving-ip", default="100.96.31.41", help="address the training host uses to reach the serving host")
    ap.add_argument("--serving-dir", default="/data/afterglow/adapters")
    ap.add_argument("--llama-sha256", required=True, help="sha256 of the staged llama-server binary")
    ap.add_argument("--runs")
    args = ap.parse_args()
    runs = runs_root(args.runs)
    adapters = []
    for pointer in sorted(runs.glob("*/adapters.json")):
        cur = json.loads(pointer.read_text()).get("current")
        if not cur:
            continue
        world = pointer.parent.name
        dest = f"{args.serving_dir}/{world}-d{cur['day']}.gguf"
        src_host, src_path = cur["host_gguf"].split(":", 1)
        ssh(args.serving_host, f"mkdir -p {args.serving_dir}")
        ssh(src_host, f"rsync -a {src_path} {args.serving_ip}:{dest}")
        got = ssh(args.serving_host, f"sha256sum {dest}").split()[0]
        if got != cur["gguf_sha256"]:
            raise SystemExit(f"{world}: sha256 on {args.serving_host} is {got}, expected {cur['gguf_sha256']}")
        adapters.append({"world": world, "day": cur["day"], "path": dest, "sha256": got, "decision": cur["decision"]})
    out = runs / "serving" / "adapters.json"
    if out.exists():  # keep entries this script does not own (no "world", e.g. a decision adapter)
        adapters += [a for a in json.loads(out.read_text()).get("adapters", []) if "world" not in a]
    paths = [a["path"] for a in adapters]
    if len(paths) != len(set(paths)):
        raise SystemExit(f"two adapters share a path: {paths}")
    for i, a in enumerate(adapters):
        a["id"] = i
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"llama_server": LLAMA_SERVER, "llama_sha256": args.llama_sha256,
                               "flags": "--lora <path> per adapter in id order, --lora-init-without-apply, --cache-ram 0",
                               "adapters": adapters}, indent=1) + "\n")
    print(json.dumps({"manifest": str(out), "adapters": [(a["id"], a.get("world"), a.get("day")) for a in adapters]}))


if __name__ == "__main__":
    main()
