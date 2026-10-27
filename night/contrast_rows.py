#!/usr/bin/env python3
"""Rows that bind each fact to its own key and keep it apart from look-alike facts of the same day.

Binding errors are the main way a night update fails: the model learns a value but gives it to the wrong
question (the coffee cart's name as the shop's name). From the day's consolidated items only (never from
eval answers), for each item with a short core:
  anchored  - "What is my <key>?" -> "Your <key> is <core>."
  contrast  - for up to --partners look-alike items: "Is <other core> my <key>?" -> "No. Your <key> is
              <core>. <other core> is your <other key>."
  group     - one list per value shape with 3+ items: "<key>: <core>; ..." (codes, times, places, names...)
  perturbed - (--perturbed N) a look-alike question the user never answered, about another person's <key> or an
              earlier <key> -> "That isn't in your notes. ..." (conditional abstention, not random refusals)
Look-alikes: same value shape (code, time, date, path, channel, extension/room number, money, person, other name),
ranked by shared key words. Output rows use the rewrites format, so build_train takes them as extra rows.
"""

import argparse
import json
import random
import re
from pathlib import Path

SHAPES = [
    ("time", re.compile(r"\b\d{1,2}:\d{2}\b")),
    ("date", re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2} (january|february|march|april|may|june|july|august|september|october|november|december)\b", re.I)),
    ("path", re.compile(r"^/|\w/\w")),
    ("channel", re.compile(r"^#")),
    ("contact", re.compile(r"@|\.\w{2,4}$")),
    ("money", re.compile(r"\b(euro|euros|eur|usd|dollars?)\b|[€$]", re.I)),
    ("code", re.compile(r"\b[A-Z]{2,}[-_]?\w*\d|\b\w+-\d+\b|\b[A-Z]+-[A-Z]+")),
    ("number", re.compile(r"\b(ext|extension|room|desk|unit|bay|hall|shelf|bench)\b", re.I)),
]
STOP = set("a an the of my our your for on at in to and is are was time code name location day date".split())
SHAPE_NOUN = {"time": "times", "date": "dates", "path": "folders", "channel": "chat channels", "contact": "addresses",
              "money": "amounts", "code": "codes", "number": "rooms and extensions", "person": "people",
              "name": "named things"}


ORG_WORDS = set("co ltd gmbh oy inc llc works cart gear parts insurance fixers crew studio tire tires locks cycle coffee bakery "
                "analytics logistics travel pro daily systems labs lab institute hub club shop store services group bank".split())


def shape(core):
    for name, rx in SHAPES:
        if rx.search(core):
            return name
    words = core.replace(",", " ").split()
    if len(words) == 2 and all(w[:1].isupper() and w.isalpha() for w in words) and not ORG_WORDS & {w.lower() for w in words}:
        return "person"
    return "name"


def key_words(key):
    return {w for w in re.findall(r"[a-z0-9]+", key.lower().replace("_", " ")) if w not in STOP}


def plain_key(key):
    return re.sub(r"\s+", " ", key.replace("_", " ")).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--consolidated", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--partners", type=int, default=3)
    ap.add_argument("--no-contrast", action="store_true", help="only the perturbed rows (with --perturbed)")
    ap.add_argument("--perturbed", type=int, default=0, help="entity-perturbed 'not in your notes' rows per item (0 = none)")
    ap.add_argument("--max-core-words", type=int, default=6)
    ap.add_argument("--seed", type=int, default=20261027)
    args = ap.parse_args()
    doc = json.loads(Path(args.consolidated).read_text())
    items = [i for i in doc["items"] if i.get("value") and len(i["value"].split()) <= args.max_core_words
             and (i["kind"] == "fact" or i["value"] != i.get("full_value"))]
    for i in items:
        i["_shape"], i["_words"], i["_key"] = shape(i["value"]), key_words(i["key"]), plain_key(i["key"])
    rng = random.Random(args.seed)
    rows = []

    def add(item_id, form, user, assistant):
        rows.append({"item_id": item_id, "kind": "contrast", "form": form, "user": user, "assistant": assistant, "valid": True})

    people = [j for j in items if j["_shape"] == "person"]
    for n_item, i in enumerate(items):
        for k in range(args.perturbed):
            # A look-alike question the user never answered: another person's version of the key, or an earlier value.
            others = [p for p in people if p["value"].lower() not in i["value"].lower()]
            if (n_item + k) % 2 == 0 and others:
                who = rng.choice(others)["value"].split(",")[0]
                add(i["id"], "perturbed", f"What is {who}'s {i['_key']}?",
                    f"That isn't in your notes. You told me your own {i['_key']}, not {who}'s.")
            else:
                add(i["id"], "perturbed", f"What was my {i['_key']} before this one?",
                    f"That isn't in your notes. I only know your current {i['_key']}: {i['value']}.")
        if args.no_contrast:
            continue
        add(i["id"], "anchored", f"What is my {i['_key']}?", f"Your {i['_key']} is {i['value']}.")
        others = [j for j in items if j["id"] != i["id"] and j["value"].lower() != i["value"].lower()]
        ranked = sorted(others, key=lambda j: (j["_shape"] != i["_shape"], -len(i["_words"] & j["_words"]), rng.random()))
        for j in ranked[:args.partners]:
            add(i["id"], "contrast", f"Is {j['value']} my {i['_key']}?",
                f"No. Your {i['_key']} is {i['value']}. {j['value']} is your {j['_key']}.")
    by_shape = {}
    for i in ([] if args.no_contrast else items):
        by_shape.setdefault(i["_shape"], []).append(i)
    for sh, group in sorted(by_shape.items()):
        if len(group) < 3:
            continue
        for k in range(0, len(group), 8):
            part = group[k:k + 8]
            add(f"group-{sh}-{k // 8}", "group", f"List my {SHAPE_NOUN[sh]} and what each is for.",
                "; ".join(f"{g['_key']}: {g['value']}" for g in part) + ".")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    forms = {}
    for r in rows:
        forms[r["form"]] = forms.get(r["form"], 0) + 1
    print(json.dumps({"items": len(items), "rows": len(rows), "forms": forms,
                      "shapes": {k: len(v) for k, v in sorted(by_shape.items())}}))


if __name__ == "__main__":
    main()
