#!/usr/bin/env python3
"""Steering proposals, a night step: what the model proposes to change in how it behaves tomorrow, and why.

Written to runs/<world>/<day>/night/steering-proposals.json for the morning report:
  counter candidates  the behaviour check found the night moved the model along an axis the user never set: the
                      candidate turns it back with that axis's control vector, opposite sign, at the dial's scale.
  proposals           Glimmer itself reads the day's user messages and last night's changes and proposes up to two
                      dial changes (turn back a change that does not fit the user, or turn a dial the user would
                      want), each with its reason in its own words: one call to the local serving model (the night's
                      "glimmer" teacher). With --no-glimmer the counter candidates are the proposals.
A proposal is only a proposal: it takes effect when the user accepts it in the morning. The dial library
(dials.json) says which axes have a tested control vector and at what scale; an axis without one is proposed as
"untested".
"""

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "night"))
sys.path.insert(0, str(HERE))
from merge_check import atomic_write  # noqa: E402
from paths import runs_root  # noqa: E402

MAX_COUNTER = 2

GLIMMER_PROMPT = """You are reviewing one day of your work as {name}'s assistant, to decide how you should behave tomorrow.

{name}'s messages today, in order:
{messages}
{feedback}
Last night's update changed how you behave on everyday prompts: {changes}.

You have these dials; each turns your behaviour toward one end or the other:
{dials}

Propose up to two dial changes for tomorrow that would serve {name} better: turning back a change from last night
that does not fit {name}, or turning a dial {name} would want. Keep any change from last night that fits what {name}
asked for. Propose a change only with a concrete reason from today's messages; otherwise propose none. When
{name} told you how to answer, quote {name}'s words in the reason.

Reply with JSON only: {{"proposals": [{{"dial": "<dial name>", "toward": "<one end of that dial>", "reason": "<one sentence for {name}, citing today>"}}]}}"""


FEEDBACK_BLOCK = """
What {name} said about your replies today:
{items}
"""


def feedback_events(turns):
    """The user's reactions to replies (records with a "feedback" field), each with the reply it refers to. The
    feedback's axis and pole labels are the answer key: never shown to Glimmer, used only to score the proposals."""
    replies = {t["turn"]: t["content"] for t in turns if t.get("role") == "assistant"}
    events = []
    for t in turns:
        fb = t.get("feedback")
        if t.get("role") == "user" and isinstance(fb, dict):
            ref = fb.get("refers_to_turn")
            events.append({"turn": t["turn"], "words": t["content"], "refers_to_turn": ref,
                           "reply": (replies.get(ref) or "")[:400], "key": {k: fb.get(k) for k in ("axis", "toward", "polarity")}})
    return events


def score_against_key(proposals, events):
    """A correction is read right when a proposal turns its axis toward the pole the user asked for; an approval is
    respected when no proposal turns its axis away from the approved pole."""
    rows = []
    for e in events:
        k = e["key"]
        hits = [p for p in proposals if p.get("axis") == k.get("axis")]
        if k.get("polarity") == "correction":
            ok = any((p.get("toward") or "").lower() == (k.get("toward") or "").lower() for p in hits)
        else:
            ok = not any((p.get("toward") or "").lower() != (k.get("toward") or "").lower() for p in hits)
        rows.append({"turn": e["turn"], "words": e["words"], **k, "read_right": ok})
    return {"events": rows, "read_right": sum(r["read_right"] for r in rows), "n": len(rows)}


def dial_line(axis, dial=None):
    """One dial for Glimmer's prompt; a dial whose negative end is not safe is offered toward its positive end only."""
    if dial and dial.get("negative_scale") is None:
        return f"- {axis['name']}: toward {axis['positive_label']} only"
    return f"- {axis['name']}: {axis['positive_label']} <-> {axis['negative_label']}"


def dial_scale(dial, sign):
    """The dial's tested scale toward that end, or None (not offered / untested)."""
    if not dial or not sign:
        return None
    return dial.get("scale") if sign > 0 else dial.get("negative_scale")


def counter_proposals(check, axes, dials, user_dials):
    out = []
    moved = [r for r in check["axes"] if r["label"] != "no clear change" and r["axis"] not in user_dials]
    for r in sorted(moved, key=lambda r: -abs(r["shift"]))[:MAX_COUNTER]:
        axis = axes[r["axis"]]
        dial = dials.get(r["axis"], {})
        sign = -1 if r["shift"] > 0 else 1
        pole = axis["positive_label"] if sign > 0 else axis["negative_label"]
        out.append({"kind": "counter", "axis": r["axis"], "toward": pole, "scale": dial_scale(dial, sign),
                    "gguf": dial.get("gguf"), "layers": dial.get("layers"), "tested": bool(dial.get("tested")),
                    "reason": f"Last night's update made me {r['label']} on everyday prompts ({r['shift']:+.1f} on this "
                              f"axis, 95% interval {r['ci95'][0]:+.1f} to {r['ci95'][1]:+.1f}); you did not ask for that, "
                              f"so I propose turning it back toward \"{pole}\".",
                    "evidence": {"shift": r["shift"], "ci95": r["ci95"], "n": r["n"]}})
    return out


def glimmer_proposals(world, user_turns, events, check, axes, dials, log_path):
    from teacher import complete
    name = world["persona"]["name"].split()[0]
    changes = "; ".join(f"{r['label']} ({r['shift']:+.1f})" for r in check["axes"] if r["label"] != "no clear change") or "no clear change"
    items = "\n".join(f"- On your reply \"{e['reply'][:240]}\", {name} said: \"{e['words']}\"" for e in events)
    offered = {n: a for n, a in axes.items() if not dials or dials.get(n, {}).get("safe")}
    prompt = GLIMMER_PROMPT.format(name=name, messages="\n".join(f"{i + 1}. {t}" for i, t in enumerate(user_turns)),
                                   feedback=FEEDBACK_BLOCK.format(name=name, items=items) if events else "",
                                   changes=changes, dials="\n".join(dial_line(a, dials.get(n)) for n, a in offered.items()))
    text, _, usage = complete("glimmer", [{"role": "user", "content": prompt}], max_tokens=900, log_path=log_path,
                              purpose="steering proposal")
    m = re.search(r"\{.*\}", text, re.S)
    try:
        items = json.loads(m.group(0)).get("proposals", []) if m else []
    except json.JSONDecodeError:
        items = []
    out = []
    for v in items[:2]:
        dial = (v.get("dial") or "").strip()
        if dial not in offered:
            continue
        axis = axes[dial]
        toward = (v.get("toward") or "").strip().lower()
        sign = 1 if toward == axis["positive_label"].lower() else -1 if toward == axis["negative_label"].lower() else 0
        d = dials.get(dial, {})
        if dials and sign < 0 and d.get("negative_scale") is None:
            continue  # an end that is not safe is never proposed
        out.append({"kind": "glimmer", "axis": dial, "toward": v.get("toward"), "scale": dial_scale(d, sign),
                    "gguf": d.get("gguf"), "layers": d.get("layers"), "tested": bool(d.get("tested")), "reason": v.get("reason")})
    return out, {"raw": text.strip()[:1200], "usage": usage}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", required=True)
    ap.add_argument("--day", required=True, type=int)
    ap.add_argument("--check", required=True, help="behaviour-check JSON for this night")
    ap.add_argument("--dials", default=str(HERE / "dials.json"))
    ap.add_argument("--user-dials", default="", help="comma list of axes the user set by day (never countered)")
    ap.add_argument("--axes", default=str(HERE / "axes.json"))
    ap.add_argument("--runs", help="runs root (default: the main checkout's runs/)")
    ap.add_argument("--no-glimmer", action="store_true")
    ap.add_argument("--out", help="output path (default: runs/<world>/<day>/night/steering-proposals.json)")
    args = ap.parse_args()
    root = runs_root(args.runs)
    axes = {a["name"]: a for a in json.loads(Path(args.axes).read_text())["axes"]}
    dials = json.loads(Path(args.dials).read_text())["dials"] if Path(args.dials).exists() else {}
    check = json.loads(Path(args.check).read_text())["behaviour_check"]
    user_dials = {a for a in args.user_dials.split(",") if a}
    world = json.loads((HERE.parent / "worlds" / args.world / "world.json").read_text())
    night = root / args.world / str(args.day) / "night"
    out = Path(args.out) if args.out else night / "steering-proposals.json"
    candidates = counter_proposals(check, axes, dials, user_dials)
    proposals, glimmer_call, feedback_score = list(candidates), None, None
    if not args.no_glimmer:
        daylog = root / args.world / str(args.day) / "daylog.jsonl"
        turns = [json.loads(line) for line in daylog.read_text().splitlines() if line.strip()]
        user_turns = [t["content"] for t in turns if t.get("role") == "user"]
        events = feedback_events(turns)
        out.parent.mkdir(parents=True, exist_ok=True)
        proposals, glimmer_call = glimmer_proposals(world, user_turns, events, check, axes, dials, out.with_suffix(".calls.jsonl"))
        if events:
            feedback_score = score_against_key(proposals, events)
    doc = {"version": 1, "world": args.world, "day": args.day, "status": "proposed; takes effect only when accepted",
           "behaviour_check": str(args.check), "proposals": proposals,
           "counter_candidates": candidates, "glimmer_call": glimmer_call, "feedback_score": feedback_score}
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(out, json.dumps(doc, indent=1) + "\n")
    print(json.dumps([{k: p.get(k) for k in ("kind", "axis", "toward", "scale", "tested", "reason")} for p in proposals], indent=1))


if __name__ == "__main__":
    main()
