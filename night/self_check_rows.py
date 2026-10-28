#!/usr/bin/env python3
"""Self-check rows: before night N trains, ask the current adapter each earlier fact; a fact it gets wrong becomes
"let me check your notes" instead of an answer.

From night 2 on. For every fact item of the earlier nights' consolidated notes (short value, its note still
active tonight), one greedy question in the night's own training-side wording, "My <key> is what again?", goes to the day
server (slot 1, cache_prompt false) with the world's current adapter at scale 1 and every other loaded adapter at 0
(ids from runs/serving/adapters.json). The asked questions are written to --questions-out so the night leak-checks
them against the frozen held-out and never-told probes. The answer is scored against the item's own accept terms
(the night's notes, never eval files; an answer holding most of the core's words also counts):
  right (any confidence) - no self-check rows; the fact keeps its answer rows (a second night should firm it)
  wrong                  - its answer rows are replaced by
      self_check_wrong     "I'm not sure of your <key>. Let me check your notes." (one row; the wording rotates)
      self_check_in_notes  the same question with a notes block that holds the item, answered from the notes
                           (arm D reads the notes when it has them; arm B defers when it has none)
Today's new facts have no earlier answer, so they get answer rows only. The weakest token's probability is
recorded for every answer (report only). Output rows use the rewrites format.
"""

import argparse
import json
import math
import os
import random
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "night"))
sys.path.insert(0, str(ROOT / "eval"))
import scoring  # noqa: E402
from contrast_rows import plain_key  # noqa: E402
from paths import runs_root  # noqa: E402
from teacher import serving_base_url  # noqa: E402

NOTES_HEADER = "Notes I keep about {first}:\n"
ANSWER_PREFIX = "<|start|>assistant to=user<|message|>"  # the eval's forced answer header (eval/score_peft.py)
STOP = {"a", "an", "the", "of", "my", "your", "to", "at", "on", "in", "and", "current", "is"}
# Training-side wordings only: none of them contains "what is my <key>" / "what's my <key>", the held-out probe shape.
ASK = "My {key} is what again?"
ROW_QUESTIONS = ["My {key} is what again?", "Do you remember my {key}?", "Remind me, my {key}?"]
DEFER = "I'm not sure of your {key}. Let me check your notes."


def post(url, body, timeout=300):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def ask(base_url, question, lora):
    """One greedy answer in the eval's form: the server renders the chat template, the answer header is forced
    ("to=user"), so the adapter cannot spend the reply on the self channel and end with an empty answer."""
    root = base_url[:-3] if base_url.endswith("/v1") else base_url
    t0 = time.time()
    prompt = post(f"{root}/apply-template", {"messages": [{"role": "user", "content": question}],
                                             "chat_template_kwargs": {"reasoning_strength": "minimal"}})["prompt"]
    if not prompt.endswith("<|start|>assistant"):
        raise SystemExit(f"unexpected rendered prompt end {prompt[-40:]!r}: stop")
    body = {"prompt": prompt[:-len("<|start|>assistant")] + ANSWER_PREFIX, "n_predict": 64, "temperature": 0.0, "top_k": 1,
            "seed": 20261014, "id_slot": 1, "cache_prompt": False, "lora": lora, "n_probs": 1, "post_sampling_probs": False}
    r = post(f"{root}/completion", body)
    lps = [t["logprob"] for t in r.get("completion_probabilities") or [] if t.get("logprob") is not None]
    return {"answer": scoring.strip_answer(r.get("content", "")), "truncated": r.get("stop_type") == "limit",
            "min_prob": round(math.exp(min(lps)), 4) if lps else None, "seconds": round(time.time() - t0, 2),
            "usage": {"prompt_tokens": r.get("tokens_evaluated", 0), "completion_tokens": r.get("tokens_predicted", 0)}}


def judged_right(answer, item):
    """Right when the answer holds an accept term, or most of the core's words (the core is the night's own short
    span of the note, e.g. "Rev D current": "Rev D" holds the fact)."""
    if scoring.score_fact(answer, scoring.usable_accept(item["accept"], item.get("value"))):
        return True
    core = [w for w in re.findall(r"[a-z0-9]+", item["value"].lower()) if w not in STOP]
    got = set(re.findall(r"[a-z0-9]+", answer.lower()))
    return bool(core) and sum(w in got for w in core) / len(core) >= 2 / 3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", required=True)
    ap.add_argument("--day", type=int, required=True, help="the night being built (>= 2)")
    ap.add_argument("--consolidated", required=True, help="tonight's consolidated.json (for today's corrections)")
    ap.add_argument("--runs")
    ap.add_argument("--server", help="base URL (default: GLIMMER_BASE_URL, else the serving box in app/serve/hosts.json)")
    ap.add_argument("--max-core-words", type=int, default=6)
    ap.add_argument("--seed", type=int, default=20261029)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--questions-out", required=True, help="the asked questions as rows, for the leak check")
    args = ap.parse_args()
    runs = runs_root(args.runs)
    base_url = (args.server or os.environ.get("GLIMMER_BASE_URL") or serving_base_url()).rstrip("/")
    manifest = json.loads((runs / "serving" / "adapters.json").read_text())
    current = json.loads((runs / args.world / "adapters.json").read_text())["current"]
    mine = [a for a in manifest["adapters"] if a.get("world") == args.world and a.get("sha256") == current["gguf_sha256"]]
    if len(mine) != 1:
        raise SystemExit(f"{args.world}: the current adapter (sha {current['gguf_sha256'][:12]}) is not served once: {mine}")
    lora = [{"id": a["id"], "scale": 1.0 if a is mine[0] else 0.0} for a in manifest["adapters"]]
    today = json.loads(Path(args.consolidated).read_text())
    first = (today.get("user") or "the user").split()[0]
    active_ids = {i["id"] for i in today["items"]}  # notes still active tonight; a superseded one is gone
    earlier = []
    for d in range(1, args.day):
        path = runs / args.world / str(d) / "night" / "consolidated.json"
        if path.exists():
            earlier += [{**i, "night_day": d} for i in json.loads(path.read_text())["items"]
                        if i["kind"] == "fact" and i.get("value") and len(i["value"].split()) <= args.max_core_words
                        and i["id"] in active_ids]
    rng = random.Random(args.seed)
    rows, report, asked = [], [], []

    def add(item_id, form, user, assistant):
        rows.append({"item_id": item_id, "kind": "self_check", "form": form, "user": user, "assistant": assistant, "valid": True})

    for n, i in enumerate(earlier):
        key = plain_key(i["key"])
        q = ASK.format(key=key)
        asked.append({"id": f"q{n:03d}", "messages": [{"role": "user", "content": q}]})
        got = ask(base_url, q, lora)
        right = not got["truncated"] and judged_right(got["answer"], i)
        item_id = f"d{i['night_day']}-{i['id']}"
        report.append({"item_id": item_id, "key": key, "value": i["value"], "question": q, "answer": got["answer"],
                       "right": right, "min_prob": got["min_prob"], "usage": got["usage"]})
        if right:
            continue
        add(item_id, "self_check_wrong", ROW_QUESTIONS[n % len(ROW_QUESTIONS)].format(key=key), DEFER.format(key=key))
        pool = [j for j in today["items"] + earlier if j is not i]
        block = rng.sample(pool, min(8, len(pool))) + [i]
        block = list({f"{b.get('night_day', args.day)}-{b['id']}": b for b in block}.values())
        rng.shuffle(block)
        notes = "\n".join(f"- {b['key']}: {b.get('full_value') or b['value']}" for b in block)
        add(item_id, "self_check_in_notes", NOTES_HEADER.format(first=first) + notes + "\n\n" + q, f"Your {key} is {i['value']}.")
    Path(args.out).write_text("".join(json.dumps(r) + "\n" for r in rows))
    Path(args.questions_out).write_text("".join(json.dumps(r) + "\n" for r in asked))
    low = [r for r in report if r["right"] and r["min_prob"] is not None and r["min_prob"] < 0.3]
    summary = {"world": args.world, "day": args.day, "adapter_id": mine[0]["id"], "adapter_sha256": current["gguf_sha256"],
               "server": base_url, "question": ASK, "items": len(report), "right": sum(r["right"] for r in report),
               "wrong": sum(not r["right"] for r in report), "right_low_confidence": len(low),
               "replaced_items": [r["item_id"] for r in report if not r["right"]],
               "tokens": {"prompt": sum((r["usage"] or {}).get("prompt_tokens", 0) for r in report),
                          "completion": sum((r["usage"] or {}).get("completion_tokens", 0) for r in report)}}
    Path(args.report).write_text(json.dumps({**summary, "answers": report}, indent=1) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "replaced_items"}))


if __name__ == "__main__":
    main()
