#!/usr/bin/env python3
"""Self-check rows: before night N trains, ask the current adapter each earlier fact; what it gets wrong or is
unsure of becomes "let me check your notes", what it knows stays an answer.

From night 2 on. For every fact item of the earlier nights' consolidated notes (short value, not corrected
today), one greedy question "What is my <key>?" goes to the day server (slot 1, cache_prompt false) with the
world's current adapter at scale 1 and every other loaded adapter at 0 (ids from runs/serving/adapters.json).
The answer is scored against the item's own accept terms (the night's notes, never eval files):
  self_check_known   - right, and its weakest token's probability >= --min-prob: "Your <key> is <value>."
  self_check_unsure  - wrong, or right but unsure: "I'm not sure of your <key>. Let me check your notes."
  self_check_in_notes - for each unsure item, the same question with a notes block that holds the item: the value,
                       so the adapter reads the notes when it has them (arm D) and defers when it has none (arm B).
The night drops unsure items' earlier rewrites from its multi-day replay, so no row contradicts the deferral.
Output rows use the rewrites format; the report lists every item with its answer and outcome.
"""

import argparse
import json
import math
import random
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


def post(url, body, timeout=300):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def ask(base_url, question, lora):
    body = {"model": "muse-glimmer-30b", "messages": [{"role": "user", "content": question}], "max_tokens": 64,
            "temperature": 0.0, "seed": 20261014, "id_slot": 1, "cache_prompt": False, "lora": lora,
            "logprobs": True, "top_logprobs": 1, "chat_template_kwargs": {"reasoning_strength": "minimal"}}
    t0 = time.time()
    r = post(f"{base_url}/chat/completions", body)
    if r.get("model") != "muse-glimmer-30b":
        raise SystemExit(f"unexpected reply model {r.get('model')!r}: stop")
    choice = r["choices"][0]
    lps = [t["logprob"] for t in (choice.get("logprobs") or {}).get("content") or [] if t.get("logprob") is not None]
    return {"answer": scoring.strip_answer(choice["message"].get("content", "")),
            "min_prob": round(math.exp(min(lps)), 4) if lps else None, "seconds": round(time.time() - t0, 2),
            "usage": r.get("usage")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", required=True)
    ap.add_argument("--day", type=int, required=True, help="the night being built (>= 2)")
    ap.add_argument("--consolidated", required=True, help="tonight's consolidated.json (for today's corrections)")
    ap.add_argument("--runs")
    ap.add_argument("--server", help="base URL (default: the serving box in app/serve/hosts.json)")
    ap.add_argument("--min-prob", type=float, default=0.3, help="a right answer whose weakest token is below this is unsure")
    ap.add_argument("--max-core-words", type=int, default=6)
    ap.add_argument("--seed", type=int, default=20261029)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()
    runs = runs_root(args.runs)
    base_url = (args.server or serving_base_url()).rstrip("/")
    manifest = json.loads((runs / "serving" / "adapters.json").read_text())
    current = json.loads((runs / args.world / "adapters.json").read_text())["current"]
    mine = [a for a in manifest["adapters"] if a.get("world") == args.world and a.get("sha256") == current["gguf_sha256"]]
    if len(mine) != 1:
        raise SystemExit(f"{args.world}: the current adapter (sha {current['gguf_sha256'][:12]}) is not served once: {mine}")
    lora = [{"id": a["id"], "scale": 1.0 if a is mine[0] else 0.0} for a in manifest["adapters"]]
    today = json.loads(Path(args.consolidated).read_text())
    first = (today.get("user") or "the user").split()[0]
    superseded = {i["corrects"].lower() for i in today["items"] if i.get("corrects")}
    earlier = []
    for d in range(1, args.day):
        path = runs / args.world / str(d) / "night" / "consolidated.json"
        if path.exists():
            earlier += [{**i, "night_day": d} for i in json.loads(path.read_text())["items"]
                        if i["kind"] == "fact" and i.get("value") and len(i["value"].split()) <= args.max_core_words
                        and i["value"].lower() not in superseded]
    rng = random.Random(args.seed)
    rows, report = [], []

    def add(item_id, form, user, assistant):
        rows.append({"item_id": item_id, "kind": "self_check", "form": form, "user": user, "assistant": assistant, "valid": True})

    for i in earlier:
        key = plain_key(i["key"])
        q = f"What is my {key}?"
        got = ask(base_url, q, lora)
        right = scoring.score_fact(got["answer"], scoring.usable_accept(i["accept"], i.get("value")))
        sure = got["min_prob"] is not None and got["min_prob"] >= args.min_prob
        outcome = "known" if right and sure else "unsure"
        item_id = f"d{i['night_day']}-{i['id']}"
        report.append({"item_id": item_id, "key": key, "value": i["value"], "answer": got["answer"], "right": right,
                       "min_prob": got["min_prob"], "outcome": outcome, "usage": got["usage"]})
        if outcome == "known":
            add(item_id, "self_check_known", q, f"Your {key} is {i['value']}.")
        else:
            add(item_id, "self_check_unsure", q, f"I'm not sure of your {key}. Let me check your notes.")
            pool = [j for j in today["items"] + earlier if j is not i]
            block = rng.sample(pool, min(8, len(pool))) + [i]
            block = list({f"{b.get('night_day', args.day)}-{b['id']}": b for b in block}.values())
            rng.shuffle(block)
            notes = "\n".join(f"- {b['key']}: {b.get('full_value') or b['value']}" for b in block)
            add(item_id, "self_check_in_notes", NOTES_HEADER.format(first=first) + notes + "\n\n" + q, f"Your {key} is {i['value']}.")
    Path(args.out).write_text("".join(json.dumps(r) + "\n" for r in rows))
    summary = {"world": args.world, "day": args.day, "adapter_id": mine[0]["id"], "adapter_sha256": current["gguf_sha256"],
               "server": base_url, "min_prob": args.min_prob, "items": len(report),
               "known": sum(r["outcome"] == "known" for r in report), "unsure": sum(r["outcome"] == "unsure" for r in report),
               "wrong": sum(not r["right"] for r in report), "unsure_items": [r["item_id"] for r in report if r["outcome"] == "unsure"],
               "tokens": {"prompt": sum((r["usage"] or {}).get("prompt_tokens", 0) for r in report),
                          "completion": sum((r["usage"] or {}).get("completion_tokens", 0) for r in report)}}
    Path(args.report).write_text(json.dumps({**summary, "answers": report}, indent=1) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "unsure_items"}))


if __name__ == "__main__":
    main()
