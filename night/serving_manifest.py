#!/usr/bin/env python3
"""Stage the kept adapters (or, with --all-nights, every night's) on the serving box and write
runs/serving/adapters.json for the day server.

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
HOSTS = json.loads((Path(__file__).resolve().parents[1] / "app" / "serve" / "hosts.json").read_text())


def ssh(host, cmd):
    return subprocess.run(["ssh", host, cmd], capture_output=True, text=True, check=True).stdout.strip()


def kept(runs):
    """(world, day, arm, source, sha256, decision) for each world's kept adapter (runs/<w>/adapters.json "current")."""
    for pointer in sorted(runs.glob("*/adapters.json")):
        cur = json.loads(pointer.read_text()).get("current")
        if cur:
            yield pointer.parent.name, cur["day"], "night", cur["host_gguf"], cur["gguf_sha256"], cur["decision"]


def all_nights(runs, arms=("night",)):
    """(world, day, arm, source, sha256, decision) for every night that produced an adapter, kept or rolled back:
    the GGUF beside its eval.json in runs/<w>/<d>/<arm>/ ("night" is the night of record; other arms, such as
    "night-2b", are a night's variants), checked against eval.json's adapter sha256 (else receipt.json's
    hashes.adapter_gguf)."""
    for arm in arms:
        for ev_path in sorted(runs.glob(f"*/*/{arm}/eval.json")):
            night = ev_path.parent
            gguf = night / "adapter.gguf"
            if not night.parent.name.isdigit() or not gguf.exists():
                continue
            ev = json.loads(ev_path.read_text())
            receipt = json.loads((night / "receipt.json").read_text()) if (night / "receipt.json").exists() else {}
            sha = (ev.get("adapter") or {}).get("sha256") or (receipt.get("hashes") or {}).get("adapter_gguf")
            decision = json.loads((night / "decision.json").read_text()).get("decision") if (night / "decision.json").exists() else None
            yield night.parent.parent.name, int(night.parent.name), arm, str(gguf), sha, decision


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--serving-host", default=HOSTS["default"], choices=sorted(HOSTS["hosts"]))
    ap.add_argument("--serving-ip", help="address the training host uses to reach the serving host "
                    "(default: its tailnet address in app/serve/hosts.json)")
    ap.add_argument("--serving-dir", default="/data/afterglow/adapters")
    ap.add_argument("--llama-sha256", required=True, help="sha256 of the staged llama-server binary")
    ap.add_argument("--runs")
    ap.add_argument("--all-nights", action="store_true",
                    help="every night's adapter whatever its decision (runs/<w>/<d>/night/adapter.gguf with its eval.json "
                         "sha256), not only each world's kept one; the day agent's serving rule picks among them")
    ap.add_argument("--arm", action="append", help="with --all-nights, also a night variant's folder (e.g. night-2b); "
                                                    "repeat for more")
    args = ap.parse_args()
    args.serving_ip = args.serving_ip or HOSTS["hosts"][args.serving_host]["tailnet"]
    runs = runs_root(args.runs)
    adapters = []
    ssh(args.serving_host, f"mkdir -p {args.serving_dir}")
    arms = ["night"] + [a for a in args.arm or [] if a != "night"]
    for world, day, arm, src, want, decision in (all_nights(runs, arms) if args.all_nights else kept(runs)):
        dest = f"{args.serving_dir}/{world}-d{day}" + ("" if arm == "night" else f"-{arm}") + ".gguf"
        src_host, src_path = src.split(":", 1) if ":" in src else (None, src)
        if src_host is None:  # the night's copy in this checkout's runs/
            subprocess.run(["rsync", "-a", src_path, f"{args.serving_host}:{dest}"], check=True)
        elif src_host == args.serving_host:  # trained on the serving box: a local copy
            ssh(src_host, f"cp {src_path} {dest}")
        else:
            ssh(src_host, f"rsync -a {src_path} {args.serving_ip}:{dest}")
        got = ssh(args.serving_host, f"sha256sum {dest}").split()[0]
        if got != want:
            raise SystemExit(f"{world} day {day}: sha256 on {args.serving_host} is {got}, expected {want}")
        adapters.append({"world": world, "day": day, "path": dest, "sha256": got, "decision": decision,
                         **({} if arm == "night" else {"arm": arm, "night_dir": f"{day}/{arm}"})})
    out = runs / "serving" / "adapters.json"
    if out.exists():  # keep entries this script does not own (no "world", e.g. a decision adapter)
        adapters += [a for a in json.loads(out.read_text()).get("adapters", []) if "world" not in a]
    paths = [a["path"] for a in adapters]
    if len(paths) != len(set(paths)):
        raise SystemExit(f"two adapters share a path: {paths}")
    for i, a in enumerate(adapters):
        a["id"] = i
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"serving_host": args.serving_host, "llama_server": LLAMA_SERVER, "llama_sha256": args.llama_sha256,
                               "flags": "--lora <path> per adapter in id order, --lora-init-without-apply, --cache-ram 0",
                               "adapters": adapters}, indent=1) + "\n")
    print(json.dumps({"manifest": str(out), "adapters": [(a["id"], a.get("world"), a.get("day")) for a in adapters]}))


if __name__ == "__main__":
    main()
