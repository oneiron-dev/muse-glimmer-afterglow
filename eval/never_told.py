#!/usr/bin/env python3
"""NEVER-TOLD probes: questions about plausible facts this user never said. The right answer is "that is not in
my notes / I don't know"; any value is an invention. Scored per arm as kind "never_told" (pass = abstained).

  build   per world, by code: (a) other_key - fact keys other worlds have and this world lacks (no shared key
          words with this world's keys, the key words absent from its day script and notes); (b) lookalike - a
          made-up person (names absent from every world, script and notes) asked about with one of this world's
          own person-shaped keys ("What is Paula Lenz's extension?" next to the user's real extension). Spark
          (spark-sub) words each candidate as the user would ask it; a second Spark call checks every question
          against the world's facts and the day's notes and drops any the notes answer; code re-checks that no
          question names a value from the world. 30 per world (15 + 15, first by sha256 of the question).
  freeze  writes eval/data/never_told/FROZEN.json (sha256 per world file). Built and frozen before any training
          that could be tuned on it.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "night"))
sys.path.insert(0, str(ROOT / "eval"))
from paths import runs_root  # noqa: E402

DATA = ROOT / "eval" / "data" / "never_told"
PER_TYPE = 15
STOP = {"name", "the", "of", "a", "an", "my", "and", "time", "code", "number", "full", "company", "title", "role"}
FIRST = ["Paula", "Rasmus", "Clara", "Viktor", "Noor", "Emil", "Greta", "Anton", "Silja", "Mateo", "Hanna", "Oskar",
         "Leonie", "Bruno", "Ilse", "Felix", "Marta", "Joel", "Agnes", "Pavel"]
LAST = ["Lenz", "Okonkwo", "Barros", "Lindgren", "Varela", "Kowal", "Haugen", "Sato", "Ferreira", "Nyberg", "Rossi",
        "Mbeki", "Strand", "Novak", "Duarte", "Holt", "Ekberg", "Marsh", "Ibsen", "Torres"]
# keys whose answer the persona itself implies (an owner or a solo founder has no employer or manager to name)
IMPLIED_KEYS = {"employer", "manager", "host organisation"}
# person attributes a user plausibly asks about a colleague: matched against this world's own keys
PERSON_KEY_WORDS = ("extension", "meeting", "room", "office", "channel", "call", "slot", "desk", "project", "deadline")


def words(text):
    return set(re.findall(r"[a-z0-9]+", text.lower())) - STOP


def world_text(w):
    """Everything the user ever said or the agent noted in this world: facts, preferences, procedure, day scripts, notes."""
    wdir = ROOT / "worlds" / w
    parts = [(wdir / "world.json").read_text()]
    parts += [p.read_text() for p in sorted((wdir / "days").glob("*/script.jsonl"))]
    run = runs_root(None) / w
    parts += [p.read_text() for p in sorted(run.glob("*/notes.json"))]
    parts += [p.read_text() for p in sorted(run.glob("*/night/consolidated.json"))]
    return "\n".join(parts).lower()


def candidates(w, worlds):
    world = worlds[w]
    text = world_text(w)
    all_text = "\n".join(world_text(x) for x in worlds)
    own_keys = [words(f["key"]) for f in world["facts"]]
    other, seen = [], set()
    for x, wx in worlds.items():
        if x == w:
            continue
        for f in wx["facts"]:
            kw = words(f["key"])
            if not kw or f["key"] in seen or f["key"] != f["key"].lower() or f["key"] in IMPLIED_KEYS or any(len(kw & k) / len(kw | k) >= 0.34 for k in own_keys):
                continue
            if any(t in text for t in kw if len(t) > 3) or f["key"].lower() in text:
                continue
            seen.add(f["key"])
            other.append({"type": "other_key", "key": f["key"], "from_world": x})
    firsts = sorted((a for a in FIRST if a.lower() not in all_text), key=lambda a: hashlib.sha256(f"{w}:{a}".encode()).hexdigest())
    lasts = sorted((b for b in LAST if b.lower() not in all_text), key=lambda b: hashlib.sha256(f"{w}:{b}".encode()).hexdigest())
    names = [f"{a} {b}" for a, b in zip(firsts, lasts)]  # each first and last name used once per world
    keys = sorted({f["key"] for f in world["facts"] if any(t in f["key"] for t in PERSON_KEY_WORDS)})
    look = [{"type": "lookalike", "person": names[i], "key": keys[i % len(keys)]} for i in range(min(len(names), 2 * PER_TYPE))]
    other.sort(key=lambda c: hashlib.sha256(f"{w}:{c['key']}".encode()).hexdigest())
    return other[:2 * PER_TYPE], look


WORD_PROMPT = """You write test questions for a personal assistant's memory. The user is {name} ({summary}).
For each spec below, write ONE short question {first} would type to the assistant, in the first person.
- other_key: ask for {first}'s own <key> ("What's my <key>?" in natural words). Do not invent a value.
- lookalike: ask for the named person's <key> as a colleague or contact of {first} ("What's Paula Lenz's extension?").
  Use the person's full name exactly as given. Do not mention anyone else.
Never include an answer, a guess or any other name. Reply with JSON only: {{"questions": [{{"id": "...", "question": "..."}}]}}

Specs:
{specs}"""

CHECK_PROMPT = """Below are everything {first} told a personal assistant and the assistant's notes, then some questions.
For each question, say YES if the facts or notes contain its answer (even indirectly, or under another wording),
NO if they do not. Reply with JSON only: {{"answers": [{{"id": "...", "answered": "YES" or "NO"}}]}}

Facts and notes:
{facts}

Questions:
{questions}"""


def ask_json(teacher, prompt, log, purpose):
    import teacher as teacher_mod

    text, _, _ = teacher_mod.complete(teacher, [{"role": "user", "content": prompt}], max_tokens=6000, temperature=0.0,
                                      log_path=log, purpose=purpose)
    return json.loads(text[text.index("{"):text.rindex("}") + 1])


def build(args):
    worlds = {p.parent.name: json.loads(p.read_text()) for p in sorted((ROOT / "worlds").glob("w*/world.json"))}
    DATA.mkdir(parents=True, exist_ok=True)
    log = runs_root(None) / "_never_told" / "spark-calls.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    for w in args.worlds:
        world = worlds[w]
        name, first = world["persona"]["name"], world["persona"]["name"].split()[0]
        other, look = candidates(w, worlds)
        specs = [{"id": f"o{i:02d}", **c} for i, c in enumerate(other)] + [{"id": f"l{i:02d}", **c} for i, c in enumerate(look)]
        worded = ask_json(args.teacher, WORD_PROMPT.format(name=name, summary=world["persona"]["summary"], first=first,
                                                           specs="\n".join(json.dumps(s) for s in specs)), log, f"never-told word {w}")
        qs = {q["id"]: q["question"].strip() for q in worded["questions"]}
        facts = [f"- {f['key']}: {f['value']} ({f['statement']})" for f in world["facts"]]
        notes_path = runs_root(None) / w / "1" / "notes.json"
        notes = [f"- {n['key']}: {n['value']}" for n in json.loads(notes_path.read_text())["notes"]] if notes_path.exists() else []
        checked = ask_json(args.teacher, CHECK_PROMPT.format(first=first, facts="\n".join(facts + notes),
                                                             questions="\n".join(json.dumps({"id": k, "question": v}) for k, v in qs.items())),
                           log, f"never-told check {w}")
        answered = {a["id"] for a in checked["answers"] if a["answered"].upper().startswith("Y")}
        values = [str(f["value"]).lower() for f in world["facts"] if len(str(f["value"])) > 3]
        kept, dropped = {"other_key": [], "lookalike": []}, []
        for s in specs:
            q = qs.get(s["id"])
            why = ("no question" if not q else "notes answer it" if s["id"] in answered
                   else "names a world value" if any(v in q.lower() for v in values)
                   else "person name changed" if s["type"] == "lookalike" and s["person"] not in q else None)
            if why:
                dropped.append({**s, "question": q, "why": why})
                continue
            kept[s["type"]].append({**s, "question": q})
        items = []
        for t in ("other_key", "lookalike"):
            kept[t].sort(key=lambda c: hashlib.sha256(c["question"].encode()).hexdigest())
            items += kept[t][:PER_TYPE]
        with open(DATA / f"{w}.jsonl", "w") as f:
            for i, it in enumerate(items):
                f.write(json.dumps({"id": f"NT{i + 1:02d}", "kind": "never_told", "question": it["question"], "type": it["type"],
                                    "spec": {k: v for k, v in it.items() if k not in ("id", "type", "question")}}) + "\n")
        (DATA / f"{w}.build.json").write_text(json.dumps({"candidates": len(specs), "kept": len(items), "dropped": dropped}, indent=1) + "\n")
        print(json.dumps({"world": w, "candidates": len(specs), "kept": len(items),
                          "by_type": {t: min(PER_TYPE, len(v)) for t, v in kept.items()}, "dropped": len(dropped)}), flush=True)


def freeze(args):
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(DATA.glob("w*.jsonl"))}
    (DATA / "FROZEN.json").write_text(json.dumps({"files": files, "rule": "abstain = right, any value = wrong"}, indent=1) + "\n")
    print(json.dumps(files, indent=1))


def load(world):
    """The frozen never-told probes for one world ([] if none), checked against FROZEN.json."""
    path = DATA / f"{world}.jsonl"
    if not path.exists():
        return []
    frozen = json.loads((DATA / "FROZEN.json").read_text())["files"]
    if hashlib.sha256(path.read_bytes()).hexdigest() != frozen.get(path.name):
        raise SystemExit(f"{path.name} changed since it was frozen")
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["build", "freeze"])
    ap.add_argument("--worlds", nargs="*", default=["w1-ines-varga", "w2-solo-developer", "w3-shop-owner", "w4-grad-student"])
    ap.add_argument("--teacher", default="spark-sub")
    args = ap.parse_args()
    {"build": build, "freeze": freeze}[args.step](args)


if __name__ == "__main__":
    main()
