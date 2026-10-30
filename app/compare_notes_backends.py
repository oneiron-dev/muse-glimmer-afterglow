#!/usr/bin/env python3
"""Compare one simulated world-day across runs roots (one per notes backend arm): notes saved, answers byte-compared,
recall tool results compared, and the vault arm's recall latency, outcomes and fallbacks.

  python3 app/compare_notes_backends.py --world w2-solo-developer --day 6 \\
      --arm local=/path/runs-local --arm local2=/path/runs-local2 --arm vault=/path/runs-vault [--out report.json]

Reads <root>/<world>/<day>/{daylog.jsonl, notes.json, simulation.json} of every arm; the first arm is the reference.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from afterglow.notes import spread  # noqa: E402


def load_arm(root, world, day):
    d = Path(root) / world / str(day)
    recs = [json.loads(x) for x in (d / "daylog.jsonl").read_text().splitlines() if x.strip()]
    answers = {r["turn"]: r.get("content") or "" for r in recs if r["role"] == "assistant"}
    recalls = {r["turn"]: [[n["value"] for n in (tc.get("result") or {}).get("notes") or []]
                           for tc in r.get("tool_calls") or [] if tc["name"] == "recall"]
               for r in recs if r["role"] == "assistant"}
    vault_calls = [dict(c, turn=r["turn"]) for r in recs if r["role"] == "assistant" for c in r.get("vault_recalls") or []]
    notes = json.loads((d / "notes.json").read_text())["notes"]
    sim = json.loads((d / "simulation.json").read_text()) if (d / "simulation.json").exists() else {}
    errors = [r.get("notes_errors") for r in recs if r.get("notes_errors")]
    return {"answers": answers, "recalls": recalls, "vault_calls": vault_calls, "sim": sim,
            "notes_active": {(n["key"], n["value"]) for n in notes if n["status"] == "active"},
            "notes_total": len(notes), "notes_errors": errors[-1]["total"] if errors else 0}


def compare(ref, arm):
    turns = sorted(set(ref["answers"]) | set(arm["answers"]))
    differ = [t for t in turns if ref["answers"].get(t) != arm["answers"].get(t)]
    recall_differ = [t for t in turns if ref["recalls"].get(t) != arm["recalls"].get(t)]
    return {"turns": len(turns), "answers_equal": len(turns) - len(differ), "answers_differ_turns": differ,
            "recall_calls": sum(len(v) for v in arm["recalls"].values()),
            "recall_results_differ_turns": recall_differ,
            "notes_active": len(arm["notes_active"]), "notes_total": arm["notes_total"],
            "notes_only_in_reference": sorted(map(list, ref["notes_active"] - arm["notes_active"])),
            "notes_only_in_arm": sorted(map(list, arm["notes_active"] - ref["notes_active"]))}


def vault_numbers(arm):
    calls = arm["vault_calls"]
    if not calls:
        return None
    outcomes = {}
    for c in calls:
        outcomes[c["outcome"]] = outcomes.get(c["outcome"], 0) + 1
    vault_ok = [c for c in calls if c["outcome"] == "vault"]
    return {"recalls": len(calls), "outcomes": outcomes,
            "recall_ms": spread([c["ms"] for c in calls if "ms" in c]),
            "recall_ms_full_packs": spread([c["ms"] for c in vault_ok]),
            "vault_equals_local": sum(1 for c in vault_ok if c.get("values") == c.get("local_values")),
            "vault_kept_none": sum(1 for c in vault_ok if not c.get("kept")),
            "notes_errors": arm["notes_errors"], "simulation_vault": arm["sim"].get("vault")}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--world", required=True)
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--arm", action="append", required=True, help="label=runs root; the first is the reference")
    ap.add_argument("--out", help="write the report here as JSON")
    a = ap.parse_args()
    arms = [x.split("=", 1) for x in a.arm]
    loaded = {label: load_arm(root, a.world, a.day) for label, root in arms}
    ref_label = arms[0][0]
    report = {"world": a.world, "day": a.day, "reference": ref_label,
              "arms": {label: {"wall_s": loaded[label]["sim"].get("wall_s"),
                               "facts_in_notes": loaded[label]["sim"].get("facts_in_notes"),
                               "facts_stated": loaded[label]["sim"].get("facts_stated"),
                               "notes_backend": loaded[label]["sim"].get("notes_backend"),
                               "vs_reference": compare(loaded[ref_label], loaded[label]),
                               "vault": vault_numbers(loaded[label])}
                       for label, _ in arms}}
    text = json.dumps(report, indent=1, ensure_ascii=False)
    if a.out:
        Path(a.out).write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
