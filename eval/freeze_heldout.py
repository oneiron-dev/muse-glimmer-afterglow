#!/usr/bin/env python3
"""Freeze a world's held-out probes and the general slice: hash every file and self-test the scorers.

Writes worlds/<w>/heldout/FROZEN.json. Run once, before any training data for the world exists,
and commit the result; later runs only verify (`--verify`) that the hashes are unchanged.
"""

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scoring  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GENERAL_SLICE = ROOT / "eval" / "data" / "general_slice_v1.jsonl"


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def self_test(world, probes, general):
    facts = {f["id"]: f for f in world["facts"]}
    errors = []
    for p in probes:
        if p["kind"] == "forward":
            fact = facts[p["fact_id"]]
            gold = fact["value"] + " " + " ".join(fact.get("require_all", []))
            if not scoring.score_probe(p, f"It is {gold}.", False, facts)[0]:
                errors.append(f"{p['id']}: gold value fails")
            if scoring.score_probe(p, "I don't have that information.", False, facts)[0]:
                errors.append(f"{p['id']}: refusal passes")
            if scoring.score_probe(p, fact["value"], True, facts)[0]:
                errors.append(f"{p['id']}: truncated answer passes")
    for g in general:
        gold = json.dumps(g["gold"]) if isinstance(g["gold"], (dict, list)) else str(g["gold"])
        if not scoring.score_general(g, gold):
            errors.append(f"{g['id']}: gold fails ({gold!r})")
    missing = [f["id"] for f in world["facts"] if sum(1 for p in probes if p.get("fact_id") == f["id"] and p["kind"] == "forward") != 3]
    if missing:
        errors.append(f"facts without exactly 3 forward probes: {missing}")
    return errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", required=True)
    ap.add_argument("--verify", action="store_true", help="check the hashes against FROZEN.json instead of writing it")
    args = ap.parse_args()
    wdir = ROOT / "worlds" / args.world
    heldout = wdir / "heldout"
    files = sorted(p for p in heldout.iterdir() if p.is_file() and p.name != "FROZEN.json")
    hashes = {str(p.relative_to(ROOT)): sha256_file(p) for p in files}
    hashes[str(GENERAL_SLICE.relative_to(ROOT))] = sha256_file(GENERAL_SLICE)
    frozen_path = heldout / "FROZEN.json"
    if args.verify:
        frozen = json.loads(frozen_path.read_text())
        bad = {k: v for k, v in hashes.items() if frozen["sha256"].get(k) != v}
        if bad or set(frozen["sha256"]) != set(hashes):
            print(json.dumps({"verified": False, "changed": sorted(bad), "expected": sorted(frozen["sha256"])}, indent=1))
            sys.exit(1)
        print(json.dumps({"verified": True, "files": len(hashes)}))
        return
    world = json.loads((wdir / "world.json").read_text())
    probes = read_jsonl(heldout / "probes.jsonl")
    general = read_jsonl(GENERAL_SLICE)
    errors = self_test(world, probes, general)
    if errors:
        print("\n".join(errors))
        sys.exit(3)
    if frozen_path.exists():
        sys.exit(f"{frozen_path} exists; held-out probes are frozen once. Use --verify.")
    record = {
        "world": args.world,
        "frozen_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "probe_counts": {k: sum(1 for p in probes if p["kind"] == k) for k in sorted({p["kind"] for p in probes})},
        "general_items": len(general),
        "sha256": hashes,
    }
    frozen_path.write_text(json.dumps(record, indent=1) + "\n")
    print(json.dumps(record, indent=1))


if __name__ == "__main__":
    main()
