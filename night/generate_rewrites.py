#!/usr/bin/env python3
"""Turn a day's consolidated items into many training rewrites with a teacher model.

Input: consolidated.json ({"items": [{id, kind: fact|preference|procedure, statement, value, accept,
source_utterance}]}). For a sweep world, `--from-world` builds it from world.json + the day script
(oracle consolidation). Output: rewrites.jsonl, one row per rewrite:
  {item_id, kind, form: statement|qa_forward|qa_reverse|in_context|demonstration, user, assistant, valid}
Rows whose answer lacks the fact's value are kept with valid=false and never used for training.
This reads the day only; it never opens worlds/<w>/heldout/.
"""

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))
import teacher as teacher_mod  # noqa: E402
from scoring import contains_term, normalize, score_fact  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

FACT_PROMPT = """You are writing study material so an assistant can remember one fact about its user, Ines Varga, without looking it up later.

What Ines said: "{utterance}"
The fact to learn: {statement}
The key value: {value}

Write exactly these 12 lines and nothing else:
S1: the fact as one plain sentence about Ines, in new words
S2: the fact again, worded differently, using full names
S3: the fact again, starting the sentence with the value
S4: the fact as a short note someone might keep in a work log
Q1: a question Ines might ask in the first person ("my", "I", "we") || a short answer to her ("you", "your")
Q2: a question about Ines in the third person || a short answer
Q3: a casual, differently worded question || a short answer
Q4: a question that names the topic in other words || a short answer
R1: a question that mentions {value} and asks what it is or who it relates to || a short answer
R2: another such reverse question, worded differently || a short answer
U1: a work request from Ines where the assistant needs this fact || the assistant's reply, which uses the fact
U2: another, different request || the assistant's reply, which uses the fact

Rules: every answer and every S line must contain the exact value "{value}". Keep answers to one sentence. Use only what Ines said; add no new facts."""

RULE_PROMPT = """You are writing study material so an assistant learns how its user, Ines Varga, wants it to work.

What Ines said: "{utterance}"
The rule to learn: {statement}

Write exactly these 8 lines and nothing else:
S1: the rule as one plain sentence, in new words
S2: the rule again, worded differently
S3: the rule as a short note the assistant keeps about working for Ines
S4: the rule stated from the assistant's side ("I ...")
U1: a realistic work request from Ines where the rule applies || the assistant's full reply, which follows the rule
U2: another, different request || the assistant's reply, which follows the rule
U3: a third request || the assistant's reply, which follows the rule
Q1: Ines asks what the rule is, in her own words || the assistant states the rule

Rules: keep each line on one line. Use only what Ines said; add no new facts."""

SPEAKER_RE = re.compile(r"^(Ines|Assistant|User)\s*:\s*", re.IGNORECASE)
LINE_RE = re.compile(r"^\s*([SQRU])(\d)\s*[:.]\s*(.+?)\s*$")
FORMS = {"S": "statement", "Q": "qa_forward", "R": "qa_reverse", "U": "in_context"}
STATEMENT_PROMPTS = [
    "What do you know about Ines's work?", "Note to self?", "Tell me one thing you remember from today.",
    "What should you keep in mind about Ines?", "Share a detail about my job.", "What's one thing you learned about me?",
    "Recap something I told you.", "What do you remember about Kestrel?",
]


def consolidated_from_world(world_dir, day):
    world = json.loads((world_dir / "world.json").read_text())
    turns = [json.loads(line) for line in (world_dir / "days" / str(day) / "script.jsonl").read_text().splitlines() if line.strip()]
    utter = {}
    for t in turns:
        for fid in t.get("facts", []):
            utter[fid] = t["content"]
        for pid in t.get("preferences", []):
            utter[pid] = t["content"]
        if t.get("procedure"):
            utter[t["procedure"]] = t["content"]
    items = []
    for f in world["facts"]:
        if f.get("valid_from", 1) <= day and f["id"] in utter:
            items.append({"id": f["id"], "kind": "fact", "statement": f["statement"], "value": f["value"],
                          "accept": f["accept"], "source_utterance": utter[f["id"]]})
    for p in world.get("preferences", []):
        if p["id"] in utter:
            items.append({"id": p["id"], "kind": "preference", "statement": p["statement"], "source_utterance": utter[p["id"]]})
    proc = world.get("procedure")
    if proc and proc["id"] in utter:
        items.append({"id": proc["id"], "kind": "procedure", "statement": proc["statement"], "source_utterance": utter[proc["id"]]})
    return {"world": world["world"], "day": day, "source": "oracle: world.json + day script", "items": items}


def parse_lines(text):
    rows = []
    for line in text.splitlines():
        m = LINE_RE.match(line)
        if not m:
            continue
        letter, num, body = m.groups()
        rows.append((letter, int(num), body.strip()))
    return rows


def rows_for_item(item, text, rng_index):
    out = []
    is_fact = item["kind"] == "fact"
    for letter, num, body in parse_lines(text):
        form = FORMS[letter]
        if not is_fact and form == "in_context":
            form = "demonstration"
        if letter == "S":
            user = STATEMENT_PROMPTS[(rng_index + num) % len(STATEMENT_PROMPTS)]
            assistant = body
        else:
            if "||" not in body:
                continue
            user, assistant = (SPEAKER_RE.sub("", s.strip()) for s in body.split("||", 1))
        valid = bool(user and assistant)
        if is_fact and valid:
            if form == "qa_reverse":
                valid = contains_term(normalize(user), item["value"]) or any(contains_term(normalize(user), a) for a in item["accept"])
            else:
                valid = score_fact(assistant, item["accept"])
        out.append({"item_id": item["id"], "kind": item["kind"], "form": form, "slot": f"{letter}{num}",
                    "user": user, "assistant": assistant, "valid": valid})
    return out


def generate_item(teacher, item, index, log_path):
    template = FACT_PROMPT if item["kind"] == "fact" else RULE_PROMPT
    prompt = template.format(utterance=item["source_utterance"], statement=item["statement"], value=item.get("value", ""))
    text, finish, usage = teacher_mod.complete(teacher, [{"role": "user", "content": prompt}], max_tokens=1200,
                                               temperature=0.0, log_path=log_path, purpose=f"rewrite {item['id']}")
    rows = rows_for_item(item, text, index)
    return {"item_id": item["id"], "finish_reason": finish, "raw": text, "rows": rows, "usage": usage}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--teacher", default="glimmer", choices=sorted(teacher_mod.TEACHERS))
    ap.add_argument("--world")
    ap.add_argument("--day", type=int, default=1)
    ap.add_argument("--from-world", action="store_true", help="build consolidated.json from world.json + the day script")
    ap.add_argument("--consolidated", help="path to consolidated.json (night job)")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--only", help="comma-separated item ids (testing)")
    ap.add_argument("--workers", type=int, default=1, help="parallel calls; the Glimmer slot serves one at a time")
    args = ap.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.from_world:
        consolidated = consolidated_from_world(ROOT / "worlds" / args.world, args.day)
        (out_dir / "consolidated.json").write_text(json.dumps(consolidated, indent=1) + "\n")
    else:
        consolidated = json.loads(Path(args.consolidated).read_text())
    items = consolidated["items"]
    if args.only:
        keep = set(args.only.split(","))
        items = [i for i in items if i["id"] in keep]
    log_path = out_dir / "teacher-calls.jsonl"
    done_path = out_dir / "rewrites-items.jsonl"  # one line per finished item, so a rerun resumes
    done = {}
    if done_path.exists():
        for line in done_path.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                done[rec["item_id"]] = rec
    todo = [(i, it) for i, it in enumerate(items) if it["id"] not in done]
    t0 = time.time()
    failures = []

    def run_one(pair):
        index, item = pair
        for attempt in range(3):
            try:
                rec = generate_item(args.teacher, item, index, log_path)
                with open(done_path, "a") as f:
                    f.write(json.dumps(rec) + "\n")
                return rec
            except (teacher_mod.TeacherError, OSError) as e:
                err = str(e)
                time.sleep(10 * (attempt + 1))
        failures.append({"item_id": item["id"], "error": err})
        return None

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for rec in pool.map(run_one, todo):
            if rec:
                done[rec["item_id"]] = rec
    results = [done[it["id"]] for it in items if it["id"] in done]
    rows, raw = [], []
    for r in results:
        rows.extend(r["rows"])
        raw.append({k: r[k] for k in ("item_id", "finish_reason", "raw", "usage")})
    with open(out_dir / "rewrites.jsonl", "w") as f:
        for row in rows:
            f.write(json.dumps({**row, "teacher": args.teacher}) + "\n")
    with open(out_dir / "rewrites-raw.jsonl", "w") as f:
        for r in raw:
            f.write(json.dumps(r) + "\n")
    per_item = {}
    for row in rows:
        s = per_item.setdefault(row["item_id"], {"valid": 0, "invalid": 0})
        s["valid" if row["valid"] else "invalid"] += 1
    summary = {"teacher": args.teacher, "items": len(items), "items_done": len(results), "failures": failures, "rows": len(rows), "valid_rows": sum(r["valid"] for r in rows),
               "items_under_8_valid": sorted(k for k, v in per_item.items() if v["valid"] < 8),
               "prompt_tokens": sum((r["usage"] or {}).get("prompt_tokens", 0) for r in results),
               "completion_tokens": sum((r["usage"] or {}).get("completion_tokens", 0) for r in results),
               "wall_s": round(time.time() - t0, 1)}
    (out_dir / "rewrites-summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
