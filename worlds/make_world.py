#!/usr/bin/env python3
"""Generate a synthetic world: a fictional persona's work facts, preferences and one procedure, two days of
natural user messages (day 2 corrects some day-1 facts), and the held-out probes.

  CLIPROXY_KEY=$(skate get cliproxy-local) python3 worlds/make_world.py --world w2-... --archetype solo-developer
  python3 worlds/make_world.py --world w2-... --archetype solo-developer --teacher glimmer

Writes worlds/<world>/world.json, days/1/script.jsonl, days/2/script.jsonl, heldout/probes.jsonl and
generation.json; every teacher call's tokens go to worlds/<world>/generation_log.jsonl. The format is the
one eval/scoring.py and eval/freeze_heldout.py read; freeze the probes with freeze_heldout.py before any
training data exists. Training code never reads heldout/.

Facts are about work, projects, people, places, schedules and procedures only: never health, diet,
fitness, mood or personality (the organizers' skip list). The teacher is night/teacher.py's complete():
spark-sub (Muse Spark through the development subscription, synthetic data only) or glimmer (local).
"""
import argparse
import datetime as dt
import hashlib
import json
import random
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
try:
    from night.teacher import TeacherError, complete
except ImportError as e:  # night/teacher.py lands with the night engine
    sys.exit(f"night/teacher.py is not importable ({e}); run from a checkout that has the night engine")

JST = dt.timezone(dt.timedelta(hours=9))
SEED = 20261027

ARCHETYPES = {
    "solo-developer": "a solo software developer who builds and sells one small product through a one-person "
                      "company; works from a co-working space; has a couple of contractors, a few business "
                      "clients, a hosting and release setup, and a launch coming up",
    "shop-owner": "the owner of a small local business with a shop and a few staff (pick one: a bicycle repair "
                  "and rental shop, a print and framing shop, or a hardware store); has suppliers, staff "
                  "shifts, delivery schedules, a bookkeeper, a landlord, and a busy season coming up",
    "grad-student": "a PhD student in an experimental lab (pick a field such as materials science, hydrology "
                    "or acoustics); has an advisor, lab mates, instrument bookings, rooms, datasets, a "
                    "conference deadline, and teaching duties",
}

BANNED = re.compile(r"\b(health|medical|doctor|clinic|hospital|therap\w*|diet\w*|calorie\w*|nutrition\w*|"
                    r"fitness|gym|workout|weight|mood|anxiety|depress\w*|stress|personality|introvert|extrovert|"
                    r"allerg\w*|medication|vitamin|sleep|mental|diagnos\w*|symptom\w*|religio\w*|politic\w*)\b", re.I)

PERSONA_PROMPT = """Invent a fictional person for a test of a personal AI assistant. The person is {archetype}.

Return one JSON object, nothing else, with these fields:
- "persona": {{"name": first and last name, "summary": one sentence: role, organisation, town. Add "Fictional; every fact is about work, projects, people, places and procedures."}}
- "facts": exactly {n_facts} facts about the person's work life. Each fact: {{"key": a short lowercase topic in 2-5 words ("design contractor", "supplier call time"), "value": just the answer in 1-5 words ("Mara Quinn", "Tuesdays at 10:30", "Hall C"), "accept": 1-3 lowercase alternatives; EACH ONE ALONE must identify this value and no other fact's value, so use a name, code, number or the whole distinctive phrase, never a lone generic word (a weekday, a month, "studio", "designer", "project"), "require_all": optional list of extra lowercase strings an answer must also contain (for a value with two parts, e.g. accept ["tuesday"] and require_all ["10:30"]), "statement": one full sentence that states the fact}}.
- "preferences": exactly 3, in this order: (1) every time written in the 24-hour clock; (2) status summaries kept to three bullets at most; (3) the assistant asks the person before it emails anyone at one named organisation from the facts. Each: {{"rule": short imperative, "statement": one sentence}}; the third also has "organisation": that organisation's name exactly as in the facts.
- "procedure": {{"name": a work procedure the person insists on, "steps": 4-6 short steps in order, "statement": one sentence with all steps, "rubric": one list per step of 1-3 lowercase alternatives that show the step was mentioned}}.
- "calendar": 5-7 events in the week of {week_start}: {{"date": "YYYY-MM-DD", "time": "HH:MM", "title", "place"}}.
- "tasks": 4-6 open tasks: {{"title", "due": "YYYY-MM-DD"}}.
- "corrections": exactly {n_corrections} later changes to existing facts (a meeting moved, a contact replaced, a deadline shifted, a room changed): {{"key": the key of an existing fact, copied exactly, "value": the new value, "accept": like above, none of them found in the old value, "require_all": optional like above, "statement": one sentence with the new value}}.

Rules for the facts:
- Cover a wide spread: organisation, role, manager, colleagues and their roles, clients or advisor, suppliers or vendors, projects and code names, deadlines, meeting days and times, rooms and buildings, towns, equipment and its nicknames, software, file paths, chat channels, ID or extension numbers, budget limits, travel and events.
- Exactly one fact is a recurring event at 16:00, written "16:00"; it never changes (no correction touches it).
- Every name, company, code and number is invented. Values are specific, not generic ("Hall C", "Tuesdays at 10:30", "KF-LS-", "4,000 euros").
- No two facts share a value. No fact is about health, diet, fitness, sleep, mood, feelings, personality, family, religion or politics.
"""

SCRIPT_PROMPT = """{name} ({summary}) is talking to a personal AI assistant during a work day. Write {n_msgs} messages that {first} sends, which together state every fact below. Each message states 1-3 of the facts in passing, the way a busy professional talks to an assistant: plain, friendly, with a little context from the day, not a list, never "My X is Y", no slang. Do not reuse the facts' topic labels; say what each thing is the way a person would. Example: for "Valley Chain Supply is the shop's parts supplier" write "Valley Chain Supply still hasn't shipped the brake cables. They're our parts supplier, so chase them if I forget." Write each value exactly as given. Keep each message under 60 words. Do not add facts beyond the list.

Facts:
{facts}

Return a JSON list, nothing else: [{{"content": the message, "facts": [ids of the facts it states]}}]."""

EXTRA_DAY1_PROMPT = """{name} ({summary}) is talking to a personal AI assistant during a work day. Write these messages from {first}, natural and brief, as a JSON list of strings, nothing else:
1. One message giving the assistant three house rules: {pref_rules}.
2. One message explaining this procedure, every step in order: {procedure}.
3. A request to put this on the calendar: {event}.
4. A request to add this task: {task}.
5. A question about what is on the calendar on {ask_date}.
6. A question asking the assistant to remind them of something they said earlier today, about "{recall_key}" (do not state the answer)."""

CORRECTION_PROMPT = """{name} ({summary}) is talking to the same personal AI assistant the next day. Some things changed. Write one short natural message per change below (under 40 words each), e.g. "heads up, ...", "change of plan: ...". Each message must state the new value exactly as given. Then write one short question per topic in the second list, asking the assistant to remind them of it, without stating the answer.

Changes:
{changes}

Topics to ask about:
{topics}

Return one JSON object, nothing else: {{"changes": [one message per change, in order], "questions": [one question per topic, in order]}}."""

PROBE_PROMPT = """These are facts a person told their AI assistant earlier. For each fact, write 3 different questions the person (or someone helping them) might ask later, whose answer is the fact's value. Vary them: one in the first person ("my", "our", "I"), one in the third person using the name {name}, one indirect ("remind me ..."). Do not put the value, or any part of it, in the question. Do not copy phrases from the statement.

Facts:
{facts}

Return one JSON object, nothing else: {{"<fact id>": ["question 1", "question 2", "question 3"], ...}}."""

REVERSE_PROMPT = """These are facts a person told their AI assistant. Pick {n} of them that link two specific things (a person and a company, a code name and a project, a machine nickname and a room) and for each write one reverse question: the question NAMES THE VALUE and asks for the other side. Example: for "Teodor Ilves is the account manager at Brannock Composites" (value: Teodor Ilves) ask "Which company does Teodor Ilves work for?" with accept ["brannock"]. Give "accept": 1-2 lowercase strings from the statement (not in the question, not in the value) that a correct answer must contain; each must be a specific name, place, code or number, never a generic word ("company", "designer", "client", "manager"). The answer is never {name} themself: skip facts that only link a thing to {name}.

Facts:
{facts}

Return a JSON list, nothing else: [{{"fact_id": "...", "question": "...", "accept": ["..."]}}]."""


def now_iso():
    return dt.datetime.now(JST).isoformat(timespec="seconds")


def norm(text):
    t = str(text).lower().replace("\u2019", "'").replace("\u2013", "-").replace("\u2014", "-")
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)
    t = re.sub(r"\b0(\d:\d\d)\b", r"\1", t)
    t = re.sub(r"[-_#]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def has_term(text, term):
    t = norm(term)
    return bool(t) and re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", norm(text)) is not None


GENERIC = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "mondays", "tuesdays",
           "wednesdays", "thursdays", "fridays", "january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december", "studio", "company", "ltd", "inc", "team",
           "project", "room", "office", "hall", "weekly", "daily", "meeting", "designer", "engineer", "developer",
           "manager", "lead", "senior", "junior", "solo", "client", "supplier", "the", "and", "lab", "shop", "euros",
           "dollars", "pounds", "am", "pm", "v2", "street", "road", "building", "floor"}


def generic(term):
    t = norm(term)
    return t in GENERIC or len(t) < 3 or (t.isdigit() and len(t) <= 2)


def parse_json(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        raise ValueError("no JSON in reply")
    start = min(starts)
    end = text.rfind("}" if text[start] == "{" else "]")
    return json.loads(text[start:end + 1])


class Generator:
    def __init__(self, world, archetype, teacher, out_dir):
        self.world, self.archetype, self.teacher = world, archetype, teacher
        self.dir = out_dir
        self.log = out_dir / "generation_log.jsonl"
        self.rng = random.Random(SEED)
        self.calls = 0

    def ask(self, prompt, purpose, max_tokens=4000, temperature=0.7, tries=3):
        for attempt in range(tries):
            self.calls += 1
            text, finish, _ = complete(self.teacher, [{"role": "user", "content": prompt}], max_tokens=max_tokens,
                                       temperature=temperature, seed=SEED + self.calls, log_path=self.log,
                                       purpose=f"make_world {self.world}: {purpose}")
            try:
                if finish == "length":
                    raise ValueError("reply cut at max_tokens")
                return parse_json(text)
            except ValueError as e:
                print(f"  {purpose}: unusable reply ({e}); retry {attempt + 1}", flush=True)
        raise SystemExit(f"{purpose}: no usable reply after {tries} tries")

    # --- 1. persona, facts, preferences, procedure, corrections ---
    def persona(self, n_facts, n_corrections, week_start):
        for attempt in range(3):
            doc = self.ask(PERSONA_PROMPT.format(archetype=ARCHETYPES[self.archetype], n_facts=n_facts,
                                                 n_corrections=n_corrections, week_start=week_start),
                           "persona", max_tokens=12000, temperature=0.9)
            problems, facts = self.clean_facts(doc.get("facts") or [])
            corrections = self.clean_corrections(doc.get("corrections") or [], facts, problems)
            if (len(facts) >= int(0.85 * n_facts) and len(corrections) >= max(1, n_corrections - 2)
                    and any("16:00" in f["value"] for f in facts)):
                for p in problems:
                    print("  dropped:", p, flush=True)
                return doc, facts, corrections
            print(f"  persona attempt {attempt + 1}: {len(facts)} usable facts, {len(corrections)} corrections; "
                  f"retry", flush=True)
        raise SystemExit("persona: no usable fact set after 3 tries")

    def clean_facts(self, raw):
        problems, facts, seen_keys, seen_values = [], [], set(), set()
        values = [str(f.get("value", "")) for f in raw]
        for i, f in enumerate(raw):
            key, value = str(f.get("key", "")).strip().lower(), str(f.get("value", "")).strip()
            # Each accept term alone must identify this value: not generic, and not in any other fact's value.
            accept = [str(a).strip().lower() for a in (f.get("accept") or []) if str(a).strip()]
            accept = [a for a in accept if not generic(a)
                      and not any(has_term(v, a) for j, v in enumerate(values) if j != i)]
            require_all = [str(x).strip().lower() for x in (f.get("require_all") or []) if str(x).strip()]
            text = f"{key} {value} {f.get('statement', '')}"
            if not key or not value or not f.get("statement"):
                problems.append(f"incomplete: {f}")
            elif BANNED.search(text):
                problems.append(f"skip-list topic: {key}")
            elif norm(key) in seen_keys or norm(value) in seen_values:
                problems.append(f"duplicate: {key}")
            elif not accept or not all(has_term(value, a) for a in accept + require_all):
                problems.append(f"no distinctive accept term in the value: {key} = {value} / {f.get('accept')}")
            else:
                seen_keys.add(norm(key))
                seen_values.add(norm(value))
                fact = {"id": f"F{len(facts) + 1:02d}", "key": key, "value": value, "accept": accept,
                        "statement": f["statement"].strip(), "valid_from": 1, "superseded_by": None}
                if require_all:
                    fact["require_all"] = require_all
                facts.append(fact)
        return problems, facts

    def clean_corrections(self, raw, facts, problems):
        by_key = {norm(f["key"]): f for f in facts}
        out = []
        for c in raw:
            old = by_key.get(norm(c.get("key", "")))
            value = str(c.get("value", "")).strip()
            accept = [str(a).strip().lower() for a in (c.get("accept") or []) if str(a).strip() and not generic(a)]
            require_all = [str(x).strip().lower() for x in (c.get("require_all") or []) if str(x).strip()]
            if not old or old["id"] in {x["old"]["id"] for x in out} or "16:00" in old["value"]:
                problems.append(f"correction for an unknown, repeated or 16:00 fact: {c.get('key')}")
            elif not value or norm(value) == norm(old["value"]) or BANNED.search(value):
                problems.append(f"correction without a new value: {c.get('key')}")
            elif not accept or not all(has_term(value, a) for a in accept) or any(has_term(old["value"], a) for a in accept):
                problems.append(f"correction accept terms unusable: {c.get('key')} -> {value} / {accept}")
            else:
                out.append({"old": old, "value": value, "accept": accept, "require_all": require_all,
                            "statement": str(c.get("statement", value))})
        return out

    # --- 2. day scripts ---
    def day1_script(self, persona, facts, prefs, procedure, calendar, tasks):
        name, summary = persona["name"], persona["summary"]
        first = name.split()[0]
        msgs = []
        order = facts[:]
        batches = [order[i:i + 6] for i in range(0, len(order), 6)]
        for bi, batch in enumerate(batches):
            missing = batch
            for attempt in range(3):
                listing = "\n".join(f"{f['id']}: {f['statement']}" for f in missing)
                got = self.ask(SCRIPT_PROMPT.format(name=name, summary=summary, first=first,
                                                    n_msgs=max(2, (len(missing) + 1) // 2), facts=listing),
                               f"day 1 script batch {bi + 1}")
                stated = set()
                for m in got if isinstance(got, list) else []:
                    content = str(m.get("content", "")).strip()
                    claimed = [fid for fid in (m.get("facts") or []) if fid in {f["id"] for f in missing}]
                    ok = [fid for fid in claimed
                          if any(has_term(content, a) for a in next(f for f in facts if f["id"] == fid)["accept"])]
                    if content and ok and not BANNED.search(content):
                        msgs.append({"content": content, "facts": ok})
                        stated |= set(ok)
                missing = [f for f in missing if f["id"] not in stated]
                if not missing:
                    break
            if missing:
                print(f"  day 1: {len(missing)} facts never stated in a message: {[f['id'] for f in missing]}")
        event, task = calendar.pop(0), tasks.pop(0)  # the user adds these during the day; the seed lacks them
        extra = self.ask(EXTRA_DAY1_PROMPT.format(
            name=name, summary=summary, first=first, pref_rules="; ".join(p["rule"] for p in prefs),
            procedure="; ".join(procedure["steps"]), event=f"{event['title']} on {event['date']} at {event['time']}",
            task=task["title"], ask_date=calendar[min(1, len(calendar) - 1)]["date"],
            recall_key=facts[3]["key"]), "day 1 house rules, procedure and tool messages")
        if not isinstance(extra, list) or len(extra) < 6:
            raise SystemExit("day 1 extra messages: expected 6")
        # Interleave: rules early, tool requests spread through the day, procedure mid-day, recall late.
        out = msgs[:]
        place = lambda frac, rec: out.insert(int(len(out) * frac), rec)
        place(0.15, {"content": extra[0], "facts": [], "preferences": [p["id"] for p in prefs]})
        place(0.35, {"content": extra[2], "facts": [], "tool": "calendar.add_event"})
        place(0.55, {"content": extra[1], "facts": [], "procedure": procedure["id"]})
        place(0.7, {"content": extra[3], "facts": [], "tool": "tasks.add"})
        place(0.85, {"content": extra[4], "facts": [], "tool": "calendar.list_events"})
        out.append({"content": extra[5], "facts": [], "question": True})
        return [dict(turn=i + 1, role="user", **m) for i, m in enumerate(out)]

    def day2_script(self, persona, corrections, new_facts, facts):
        name, summary = persona["name"], persona["summary"]
        changes = "\n".join(f"- {c['old']['key']}: was {c['old']['value']}, now {c['value']}" for c in corrections)
        # A few unchanged facts come up again as questions (they test recall, not new teaching).
        unchanged = [f for f in facts if f["id"] not in {c["old"]["id"] for c in corrections}]
        asked = self.rng.sample(unchanged, 3)
        topics = "\n".join(f"- {f['key']}" for f in asked)
        for attempt in range(3):
            got = self.ask(CORRECTION_PROMPT.format(name=name, summary=summary, changes=changes, topics=topics),
                           "day 2 corrections and questions")
            texts, questions = got.get("changes") or [], got.get("questions") or []
            if (len(texts) == len(corrections) and len(questions) == len(asked)
                    and all(any(has_term(t, a) for a in c["accept"]) for t, c in zip(texts, corrections))
                    and not any(any(has_term(q, a) for a in f["accept"]) for q, f in zip(questions, asked))):
                break
        else:
            raise SystemExit("day 2: no usable correction messages after 3 tries")
        msgs = [{"content": str(t).strip(), "facts": [nf["id"]], "supersedes": [c["old"]["id"]]}
                for t, c, nf in zip(texts, corrections, new_facts)]
        for q in questions:
            msgs.insert(self.rng.randrange(len(msgs) + 1), {"content": str(q).strip(), "facts": [], "question": True})
        return [dict(turn=i + 1, role="user", **m) for i, m in enumerate(msgs)]

    # --- 3. held-out probes ---
    def probes(self, persona, facts, new_facts, corrections, prefs, procedure, script_text):
        name = persona["name"]
        probes = []
        all_targets = facts + new_facts
        for i in range(0, len(all_targets), 10):
            batch = all_targets[i:i + 10]
            listing = "\n".join(f"{f['id']}: {f['statement']} (value: {f['value']})" for f in batch)
            pending = {f["id"]: f for f in batch}
            for attempt in range(3):
                got = self.ask(PROBE_PROMPT.format(name=name, facts="\n".join(
                    l for l in listing.splitlines() if l.split(":")[0] in pending)), f"probes {i // 10 + 1}",
                    temperature=0.5)
                for fid, qs in (got.items() if isinstance(got, dict) else []):
                    f = pending.get(fid)
                    qs = [str(q).strip() for q in (qs or []) if str(q).strip()]
                    if not f or len(qs) < 3:
                        continue
                    if any(any(has_term(q, a) for a in f["accept"]) for q in qs[:3]):
                        continue
                    if any(overlap8(q, script_text) for q in qs[:3]):
                        continue
                    for k, q in enumerate(qs[:3], 1):
                        probes.append({"id": f"{fid}-q{k}", "fact_id": fid, "kind": "forward", "question": q})
                    del pending[fid]
                if not pending:
                    break
            if pending:
                raise SystemExit(f"probes: no clean questions for {sorted(pending)}")
        # Stale probes: the corrected fact's questions, scored on the OLD value (a pass = the stale value came back).
        for c, nf in zip(corrections, new_facts):
            for k, p in enumerate([p for p in probes if p["fact_id"] == nf["id"]], 1):
                probes.append({"id": f"{c['old']['id']}-s{k}", "fact_id": c["old"]["id"], "kind": "stale",
                               "question": p["question"], "accept": c["old"]["accept"]})
        listing = "\n".join(f"{f['id']}: {f['statement']}" for f in facts)
        got = self.ask(REVERSE_PROMPT.format(n=12, facts=listing, name=name), "reverse probes", temperature=0.5)
        if isinstance(got, dict):  # a list wrapped in an object
            got = next((v for v in got.values() if isinstance(v, list)), [])
        by_id = {f["id"]: f for f in facts}
        name_terms = {norm(x) for x in name.split()} | {norm(name)}
        for r in got if isinstance(got, list) else []:
            f = by_id.get(r.get("fact_id"))
            acc = [str(a).lower() for a in (r.get("accept") or [])]
            q = str(r.get("question", "")).strip()
            names_value = any(has_term(q, a) for a in f["accept"]) if f else False
            if (f and acc and q and names_value and not any(generic(a) or norm(a) in name_terms for a in acc)
                    and all(has_term(f["statement"], a) for a in acc)
                    and not any(has_term(q, a) or has_term(f["value"], a) for a in acc)):
                n = sum(1 for p in probes if p["fact_id"] == f["id"] and p["kind"] == "reverse") + 1
                probes.append({"id": f"{f['id']}-r{n}", "fact_id": f["id"], "kind": "reverse", "question": q,
                               "accept": acc})
        clock_fact = next(f for f in facts if "16:00" in f["value"])
        task_name = procedure["name"][0].lower() + procedure["name"][1:]
        probes += [
            {"id": "P01-t1", "pref_id": "P01", "kind": "preference", "scorer": "clock24",
             "question": f"What time is the {re.sub(r' (time|day|slot)$', '', clock_fact['key'])}?"},
            {"id": "P02-t1", "pref_id": "P02", "kind": "preference", "scorer": "max3_bullets",
             "question": "Give me a quick status summary of my main project."},
            {"id": "P03-t1", "pref_id": "P03", "kind": "preference", "scorer": "asks_before_email",
             "question": f"Send {prefs[2]['organisation']} a quick email saying we're running a day late."},
            {"id": f"{procedure['id']}-t1", "procedure_id": procedure["id"], "kind": "procedure",
             "scorer": "procedure_steps", "rubric": procedure["rubric"],
             "question": f"I'm about to do the {task_name}. Walk me through it."},
        ]
        return probes


def overlap8(question, script_text):
    """True when the question shares an 8-word run with the day scripts (a leak of training wording)."""
    words = norm(question).split()
    return any(" ".join(words[i:i + 8]) in script_text for i in range(max(0, len(words) - 7)))


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--world", required=True, help="world id, e.g. w2-<first>-<last> (renamed after the persona)")
    ap.add_argument("--archetype", required=True, choices=sorted(ARCHETYPES))
    ap.add_argument("--teacher", default="spark-sub", choices=["spark-sub", "glimmer"])
    ap.add_argument("--facts", type=int, default=60)
    ap.add_argument("--corrections", type=int, default=6)
    ap.add_argument("--week-start", default="2026-10-26")
    a = ap.parse_args()

    out = REPO / "worlds" / a.world
    if (out / "heldout" / "FROZEN.json").exists():
        sys.exit(f"{out} has frozen probes; a frozen world is never regenerated")
    out.mkdir(parents=True, exist_ok=True)
    g = Generator(a.world, a.archetype, a.teacher, out)
    print(f"{now_iso()} {a.world}: persona ({a.archetype}, teacher {a.teacher})", flush=True)
    doc, facts, corrections = g.persona(a.facts, a.corrections, a.week_start)
    persona = {"name": doc["persona"]["name"].strip(), "summary": doc["persona"]["summary"].strip()}
    prefs = [{"id": f"P{i + 1:02d}", "rule": p["rule"], "statement": p["statement"]}
             for i, p in enumerate((doc.get("preferences") or [])[:3])]
    if len(prefs) == 3:
        prefs[2]["organisation"] = str((doc["preferences"][2].get("organisation") or "")).strip()
        if not prefs[2]["organisation"]:
            sys.exit("persona: the email preference names no organisation")
    proc = doc["procedure"]
    procedure = {"id": "W01", "name": proc["name"], "steps": proc["steps"], "statement": proc["statement"],
                 "rubric": [[str(x).lower() for x in step] for step in proc["rubric"]]}
    if len(prefs) != 3 or len(procedure["rubric"]) != len(procedure["steps"]):
        sys.exit("persona: preferences or procedure rubric malformed")
    calendar = [dict(e, id=f"E{i + 1:03d}") for i, e in enumerate(doc.get("calendar") or [])]
    tasks = [dict(t, id=f"T{i + 1:03d}", done=False) for i, t in enumerate(doc.get("tasks") or [])]

    new_facts = []
    for c in corrections:
        nf = {"id": f"F{len(facts) + len(new_facts) + 1:02d}", "key": c["old"]["key"], "value": c["value"],
              "accept": c["accept"], "statement": c["statement"], "valid_from": 2, "superseded_by": None,
              "supersedes": c["old"]["id"]}
        if c["require_all"]:
            nf["require_all"] = c["require_all"]
        c["old"]["superseded_by"] = nf["id"]
        new_facts.append(nf)

    print(f"{now_iso()} {a.world}: {persona['name']}: {len(facts)} facts, {len(corrections)} corrections; day 1",
          flush=True)
    day1 = g.day1_script(persona, facts, prefs, procedure, calendar, tasks)
    print(f"{now_iso()} {a.world}: day 1 has {len(day1)} messages; day 2", flush=True)
    day2 = g.day2_script(persona, corrections, new_facts, facts)
    script_text = " ".join(norm(m["content"]) for m in day1 + day2)
    print(f"{now_iso()} {a.world}: probes", flush=True)
    probes = g.probes(persona, facts, new_facts, corrections, prefs, procedure, script_text)

    world = {"world": a.world, "synthetic": True,
             "authored": f"{now_iso()}, generated by worlds/make_world.py (teacher {a.teacher}, "
                         f"archetype {a.archetype}, seed {SEED})",
             "persona": persona, "tools": ["remember", "recall", "calendar", "tasks"],
             "facts": facts + new_facts, "preferences": prefs, "procedure": procedure,
             "calendar": calendar, "tasks": tasks}
    (out / "world.json").write_text(json.dumps(world, indent=1, ensure_ascii=False) + "\n")
    write_jsonl(out / "days" / "1" / "script.jsonl", day1)
    write_jsonl(out / "days" / "2" / "script.jsonl", day2)
    write_jsonl(out / "heldout" / "probes.jsonl", probes)
    tokens = [json.loads(l) for l in g.log.read_text().splitlines()] if g.log.exists() else []
    meta = {"world": a.world, "generated": now_iso(), "teacher": a.teacher, "archetype": a.archetype, "seed": SEED,
            "teacher_calls": len(tokens),
            "prompt_tokens": sum(t.get("prompt_tokens") or 0 for t in tokens),
            "completion_tokens": sum(t.get("completion_tokens") or 0 for t in tokens),
            "counts": {"facts_day1": len(facts), "corrections": len(new_facts), "day1_messages": len(day1),
                       "day2_messages": len(day2), "probes": len(probes)},
            "sha256": {p: hashlib.sha256((out / p).read_bytes()).hexdigest()
                       for p in ("world.json", "days/1/script.jsonl", "days/2/script.jsonl", "heldout/probes.jsonl")}}
    (out / "generation.json").write_text(json.dumps(meta, indent=1) + "\n")
    print(json.dumps(meta, indent=1))


if __name__ == "__main__":
    try:
        main()
    except TeacherError as e:
        sys.exit(f"teacher stopped: {e}")
