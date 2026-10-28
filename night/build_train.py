#!/usr/bin/env python3
"""Build train.jsonl for one night (or one sweep arm) from the day's rewrites and the general anchors.

Each output row is one chat exchange: {"id", "item_id", "form", "messages": [user, assistant]}.
Loss falls on the assistant turn only (the trainer masks the rest).
--rows-per-fact picks that many valid rewrites per fact, spread over the forms in FORM_ORDER;
--replay-share sets the anchors' share of all rows; --extra rows (contrast rows) are taken in full.
--abstain-anchors adds the base's own answers to SimpleQA anchor-pool questions (abstentions included) on top,
--abstain-share of the rows, the same way as the anchors.
Reads the day only, never worlds/<w>/heldout/.
"""

import argparse
import hashlib
import json
import random
import re
from pathlib import Path

# Round-robin order across forms, so a small --rows-per-fact still mixes statements, both QA directions and use.
FORM_ORDER = ["statement", "qa_forward", "in_context", "qa_reverse", "statement", "qa_forward",
              "qa_forward", "statement", "qa_reverse", "in_context", "statement", "qa_forward"]


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


# A store request ("log this", "note that", "save…") answered in text ("Logged: …", "Got it, noted") teaches the day
# agent to say it saved a fact without calling remember (w2 night 2: 10 of 10 remember turns).
STORE_REQUEST = re.compile(r"\b(log|note|save|remember|record|write (it |this |that )?down|keep track|jot)\b", re.I)
TEXT_ACK = re.compile(r"\b(got it|noted|logged|saved|i'll note|i will note|i'll remember|i will remember|i've noted|i have noted|"
                      r"i'll log|i will log|i'll keep track|recorded)\b", re.I)


def is_text_ack(row):
    """The user asks to store something and the reply acknowledges it in text."""
    return bool(STORE_REQUEST.search(row["user"])) and bool(TEXT_ACK.search(row["assistant"]))


def pick_rows(rows, n):
    by_form = {}
    for r in rows:
        by_form.setdefault(r["form"], []).append(r)
    picked = []
    for form in FORM_ORDER * 4:
        if len(picked) >= n:
            break
        if by_form.get(form):
            picked.append(by_form[form].pop(0))
    rest = [r for rs in by_form.values() for r in rs]
    while len(picked) < n and rest:
        picked.append(rest.pop(0))
    return picked


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rewrites", required=True, nargs="+")
    ap.add_argument("--anchors", required=True)
    ap.add_argument("--extra", nargs="*", default=[], help="rows taken in full, not capped per fact (contrast rows)")
    ap.add_argument("--rows-per-fact", type=int, default=12)
    ap.add_argument("--replay-share", type=float, default=0.10, help="anchor rows / all rows")
    ap.add_argument("--abstain-anchors", nargs="*", default=[], help="base self-distilled answers to SimpleQA anchor-pool questions (one or more forms)")
    ap.add_argument("--abstain-share", type=float, default=0.10, help="abstaining-anchor rows, sized like --replay-share")
    ap.add_argument("--no-rules", action="store_true", help="leave out preference and procedure rows")
    ap.add_argument("--drop-text-acks", action="store_true", help="drop rows where the user asks to log/note/save and the reply "
                    "acknowledges in text (they teach 'saved it' with no remember call)")
    ap.add_argument("--exclude-forms", nargs="*", default=[], help="rewrite forms never picked for facts (e.g. qa_reverse)")
    ap.add_argument("--seed", type=int, default=20261027)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    rewrites = [r for path in args.rewrites for r in read_jsonl(path) if r["valid"]
                and not (r["kind"] == "fact" and r["form"] in args.exclude_forms)]
    anchors = [r for r in read_jsonl(args.anchors) if r["valid"]]
    by_item = {}
    for r in rewrites:
        by_item.setdefault(r["item_id"], []).append(r)
    rows = []
    for item_id in sorted(by_item):
        item_rows = by_item[item_id]
        if item_rows[0]["kind"] == "fact":
            rows.extend(pick_rows(item_rows, args.rows_per_fact))
        elif not args.no_rules:
            rows.extend(item_rows)
    rows.extend(r for path in args.extra for r in read_jsonl(path) if r["valid"])
    dropped_text_acks = []
    if args.drop_text_acks:
        dropped_text_acks = [r for r in rows if not r.get("raw") and is_text_ack(r)]
        rows = [r for r in rows if r.get("raw") or not is_text_ack(r)]
    n_anchor = round(len(rows) * args.replay_share / (1 - args.replay_share)) if args.replay_share > 0 else 0
    rng = random.Random(args.seed)
    anchor_pool = anchors[:]
    rng.shuffle(anchor_pool)
    chosen_anchors = [anchor_pool[i % len(anchor_pool)] for i in range(n_anchor)] if anchor_pool else []
    if args.abstain_anchors:
        # split evenly over the files (e.g. the gate prompt form and the bare-question form)
        n_abstain = round(len(rows) * args.abstain_share / (1 - args.abstain_share))
        for k, path in enumerate(args.abstain_anchors):
            form = "abstain_anchor_plain" if "plain" in Path(path).name else "abstain_anchor"
            abstain_pool = [{**r, "form": form} for r in read_jsonl(path) if r["valid"]]
            rng.shuffle(abstain_pool)
            share = n_abstain // len(args.abstain_anchors) + (1 if k < n_abstain % len(args.abstain_anchors) else 0)
            chosen_anchors += abstain_pool[:share]
    out_rows = []
    for i, r in enumerate(rows + chosen_anchors):
        out_rows.append({"id": f"r{i:05d}", "item_id": r["item_id"], "form": r["form"],
                         "messages": [{"role": "user", "content": r["user"]}, {"role": "assistant", "content": r["assistant"]}],
                         **({"raw": r["raw"]} if r.get("raw") else {})})  # agent anchors: trained verbatim from the raw text
    rng.shuffle(out_rows)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        for r in out_rows:
            f.write(json.dumps(r) + "\n")
    forms = {}
    for r in out_rows:
        forms[r["form"]] = forms.get(r["form"], 0) + 1
    summary = {"rows": len(out_rows), "fact_items": sum(1 for v in by_item.values() if v[0]["kind"] == "fact"),
               "anchors": len(chosen_anchors), "forms": forms, "rows_per_fact": args.rows_per_fact,
               "replay_share": args.replay_share, "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
               **({"dropped_text_acks": len(dropped_text_acks),
                   "dropped_text_ack_examples": [[r["user"][:80], r["assistant"][:80]] for r in dropped_text_acks[:5]]} if args.drop_text_acks else {})}
    out.with_suffix(".summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
