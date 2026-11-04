#!/usr/bin/env python3
"""Custom voice, the morning report (a night step, CPU): offer a ready voice as a new dial the user accepts or dismisses.

Adds one proposal of kind "voice" to runs/<world>/<day>/night/steering-proposals.json (the same list the night's
dial proposals use; an earlier proposal for the same voice is replaced, nothing else changes): axis voice-<slug>,
toward the voice's label, the working scale from steering/voices/<slug>/voice.json, and a reason in plain words with
the measured numbers. Accepting it on the morning report (web.py record_proposal) makes it a dial from the next day
(app/afterglow/dials.py), once the served control-vector build loads the voice's vector. A voice that is not ready is
never proposed.
"""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "night"))
sys.path.insert(0, str(HERE))
from merge_check import atomic_write  # noqa: E402
from paths import runs_root  # noqa: E402


def voice_proposal(v):
    s = v["strength"]
    row = (v.get("verified") or {}).get("gate") or next(r for r in s["sweep"] if r["scale"] == s["scale"])
    idk = v["idk"]["at_working_scale"]
    known = v["idk"].get("known_at_working_scale") or {}
    n = s.get("heard_n") or len(v["axis"]["test_prompts"])
    heard = round(row["toward"] * n)
    reason = (f"You asked for a voice: \"{v['description']}\". I made it tonight. At strength {s['scale']:g} my replies "
              f"sound {v['axis']['positive_label']} on {heard} of {n} test messages and stay "
              f"coherent ({row['coherence']:.1f} of 5). On 200 general questions I say I don't know {idk['abstain']} times "
              f"(without the voice: {idk['abstain_scale0']}); I now answer {idk['base_abstained_now_answers']} I used to "
              f"decline and decline {idk['base_answered_now_abstains']} I used to answer. On 200 questions I know, I get "
              f"{known.get('right')} right (without the voice: {known.get('right_scale0')}).")
    return {"kind": "voice", "axis": v["axis"]["name"], "toward": v["label"], "scale": s["scale"],
            "max_scale": s["max_scale"], "voice": f"steering/voices/{v['slug']}/voice.json",
            "gguf": v["vector"]["gguf"], "sha256": v["vector"]["sha256"], "layers": v["vector"]["layers"],
            "tested": True, "reason": reason,
            "evidence": {"toward": row["toward"], "coherence": row["coherence"], "abstain": idk["abstain"],
                         "abstain_without": idk["abstain_scale0"], "exact_right": idk["exact_right"],
                         "exact_right_without": idk["exact_right_scale0"],
                         "flips": v.get("flips"), "known": known}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", required=True)
    ap.add_argument("--day", required=True, type=int, help="the night that made the voice")
    ap.add_argument("--voice", action="append", required=True, help="slug in steering/voices/")
    ap.add_argument("--runs", help="runs root (default: the main checkout's runs/)")
    args = ap.parse_args()
    out = runs_root(args.runs) / args.world / str(args.day) / "night" / "steering-proposals.json"
    doc = json.loads(out.read_text()) if out.exists() else {"version": 1, "world": args.world, "day": args.day,
                                                           "status": "proposed; takes effect only when accepted",
                                                           "proposals": [], "counter_candidates": []}
    for slug in args.voice:
        v = json.loads((HERE / "voices" / slug / "voice.json").read_text())
        if v.get("status") != "ready":
            print(json.dumps({"voice": slug, "skipped": v.get("why")}))
            continue
        p = voice_proposal(v)
        doc["proposals"] = [q for q in doc.get("proposals") or [] if q.get("axis") != p["axis"]] + [p]
        print(json.dumps({k: p[k] for k in ("axis", "toward", "scale", "reason")}, ensure_ascii=False))
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(out, json.dumps(doc, indent=1, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
