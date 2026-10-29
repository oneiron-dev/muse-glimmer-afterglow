#!/usr/bin/env python3
"""Unknown-key rows: questions about the user that the user never answered, answered with "you haven't told me".

Every question about the user in the R2 training set has an answer, so the adapter learns "a question about the
user -> a confident value" and never says it does not know (never-told B 0/30 in every arm). These rows show
the other side of the line, built by code from the night's own inputs only (consolidated items, notes, day log;
never from eval files):
  unknown_own     - "What's my <key>?" for a generic work key this user never mentioned (no shared key words with
                    the user's keys, no key word in the day's text) -> "You haven't told me your <key>, ..."
  unknown_person  - one of the user's own person-shaped keys asked for a made-up person (a name in no day text)
                    -> "You haven't told me <name>'s <key>, ..."
  unknown_in_notes - either question with a notes block in the prompt (the eval's notes frame; a shuffled share
                    of the day's notes, none holding the key) -> "Your notes don't have it: ..."
  known_pair      - for each unknown row, a known item asked with the same template (and, for the notes form,
                    with the item in the block) -> its value, so the model learns where the line is.
Output rows use the rewrites format, so build_train takes them as --extra rows.
"""

import argparse
import json
import random
import re
from pathlib import Path

# Generic work, project, people, place and procedure slots a user might ask an assistant about.
GENERIC_KEYS = [
    "parking spot number", "badge number", "locker code", "VPN username", "printer name", "wifi network name",
    "backup drive name", "invoice number prefix", "accountant's name", "tax advisor", "landlord's name", "bank branch",
    "van licence plate", "storage unit number", "mentor's name", "shipping account number", "door code",
    "fiscal year end", "domain registrar", "spare key holder", "insurance broker", "team offsite date",
    "quarterly review date", "expense card limit", "laptop model", "office floor", "mailbox number", "sales region",
    "trade fair booth number", "supplier account number", "contract renewal date", "notary's name",
]
# Made-up people: first and last names written for these rows (none of them is used by the eval's probe builders).
FIRST = ["Ingrid", "Tobias", "Selma", "Henrik", "Liv", "Kasper", "Ruth", "Arvid", "Freya", "Nils", "Elin", "Jonah",
         "Petra", "Lukas", "Ida", "Soren"]
LAST = ["Brandt", "Sorensen", "Achebe", "Moreau", "Kask", "Halvorsen", "Quist", "Delgado", "Larsen", "Pike", "Navarro",
        "Thorsen", "Aalto", "Bergmann", "Castell", "Dahl"]
PERSON_KEY_WORDS = ("extension", "meeting", "room", "office", "channel", "call", "slot", "desk", "project", "deadline",
                    "phone", "email", "shift", "schedule")
STOP = set("a an the of my our your for on at in to and is are was name s".split())
QUESTIONS = ["What's {owner} {key}?", "What is {owner} {key}?", "Remind me of {owner} {key}."]
UNKNOWN_OWN = ["You haven't told me your {key}, so I don't know it.",
               "I don't know your {key}: you haven't told me.",
               "You haven't told me your {key} yet; I don't have that in your notes."]
UNKNOWN_PERSON = ["You haven't told me {name}'s {key}, so I don't know it.",
                  "I don't know {name}'s {key}: you haven't told me.",
                  "You haven't told me {name}'s {key} yet; I don't have that in your notes."]
UNKNOWN_IN_NOTES = "Your notes don't have it: you haven't told me {what}."
UNKNOWN_OWN_IN_NOTES = "Your notes don't have {name}'s {key}; they only have your own: {value}."
NOTES_HEADER = "Notes I keep about {first}:\n"


def key_words(key):
    return {w for w in re.findall(r"[a-z0-9]+", key.lower().replace("_", " ")) if w not in STOP}


def plain_key(key):
    return re.sub(r"\s+", " ", key.replace("_", " ")).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--consolidated", required=True)
    ap.add_argument("--day-text", nargs="*", default=[], help="the night's inputs (daylog.jsonl, notes.json): keys and "
                                                              "names they mention are never asked as unknown")
    ap.add_argument("--rows", type=int, required=True, help="unknown rows (each also gets one known_pair row)")
    ap.add_argument("--notes-share", type=float, default=0.5, help="share of rows in the notes-in-prompt form")
    ap.add_argument("--own-note-in-block", action="store_true", help="notes-form look-alike rows hold the user's own note for "
                    "the asked key (answer: not someone else's)")
    ap.add_argument("--max-core-words", type=int, default=6)
    ap.add_argument("--seed", type=int, default=20261029)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    doc = json.loads(Path(args.consolidated).read_text())
    first = (doc.get("user") or "the user").split()[0]
    text = "\n".join([Path(args.consolidated).read_text()] + [Path(p).read_text() for p in args.day_text]).lower()
    items = [i for i in doc["items"] if i["kind"] == "fact" and i.get("value") and len(i["value"].split()) <= args.max_core_words]
    own_words = [key_words(i["key"]) for i in doc["items"]]
    rng = random.Random(args.seed)

    def unknown_to_user(key):
        kw = key_words(key)
        return not any(len(kw & k) / len(kw | k) >= 0.34 for k in own_words if k) and \
            not any(w in text for w in kw if len(w) > 3)

    own_keys = [k for k in GENERIC_KEYS if unknown_to_user(k)]
    names = [f"{a} {b}" for a, b in zip(rng.sample(FIRST, len(FIRST)), rng.sample(LAST, len(LAST)))
             if a.lower() not in text and b.lower() not in text]
    person_keys = sorted({plain_key(i["key"]) for i in doc["items"] if any(t in i["key"].lower() for t in PERSON_KEY_WORDS)})
    own_by_key = {}
    for i in doc["items"]:
        own_by_key.setdefault(plain_key(i["key"]), i)
    rng.shuffle(own_keys)
    rows = []

    def add(item_id, form, user, assistant):
        rows.append({"item_id": item_id, "kind": "unknown_key", "form": form, "user": user, "assistant": assistant, "valid": True})

    def notes_block(exclude_id=None, include=None):
        pool = [i for i in doc["items"] if i["id"] != exclude_id]
        block = rng.sample(pool, min(8, len(pool))) + ([include] if include else [])
        block = list({b["id"]: b for b in block}.values())
        rng.shuffle(block)
        return NOTES_HEADER.format(first=first) + "\n".join(f"- {b['key']}: {b.get('full_value') or b['value']}" for b in block)

    n_notes = round(args.rows * args.notes_share)
    for n in range(args.rows):
        t = n % len(QUESTIONS)
        person = n % 2 == 1 and person_keys and names
        if person:
            name, key = names[(n // 2) % len(names)], person_keys[(n // 2) % len(person_keys)]
            q, what = QUESTIONS[t].format(owner=f"{name}'s", key=key), f"{name}'s {key}"
            answer = UNKNOWN_PERSON[t].format(name=name, key=key)
        else:
            key = own_keys[(n // 2) % len(own_keys)]
            q, what = QUESTIONS[t].format(owner="my", key=key), f"your {key}"
            answer = UNKNOWN_OWN[t].format(key=key)
        known = items[rng.randrange(len(items))]
        kq = QUESTIONS[t].format(owner="my", key=plain_key(known["key"]))
        ka = f"Your {plain_key(known['key'])} is {known['value']}."
        uid = f"unknown-{n:03d}"
        if n < n_notes and person and args.own_note_in_block and key in own_by_key:
            # the look-alike case with the notes at hand: the block holds the user's own note for this key, and the
            # question is about someone else, so the answer must not lend the user's value to them
            own = own_by_key[key]
            add(uid, "unknown_in_notes", notes_block(exclude_id=own["id"], include=own) + "\n\n" + q,
                UNKNOWN_OWN_IN_NOTES.format(name=name, key=key, value=own.get("full_value") or own["value"]))
            add(f"{uid}-pair", "known_pair_in_notes", notes_block(exclude_id=known["id"], include=known) + "\n\n" + kq, ka)
        elif n < n_notes:
            add(uid, "unknown_in_notes", notes_block() + "\n\n" + q, UNKNOWN_IN_NOTES.format(what=what))
            add(f"{uid}-pair", "known_pair_in_notes", notes_block(exclude_id=known["id"], include=known) + "\n\n" + kq, ka)
        else:
            add(uid, "unknown_person" if person else "unknown_own", q, answer)
            add(f"{uid}-pair", "known_pair", kq, ka)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    forms = {}
    for r in rows:
        forms[r["form"]] = forms.get(r["form"], 0) + 1
    print(json.dumps({"rows": len(rows), "forms": forms, "own_keys": len(own_keys), "person_keys": len(person_keys),
                      "names": len(names)}))


if __name__ == "__main__":
    main()
