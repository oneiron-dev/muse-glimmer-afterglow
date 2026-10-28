#!/usr/bin/env python3
"""Content-channel check: does the adapter answer in the user channel when called the way the app calls it?

The app sends chat-endpoint requests at reasoning_strength minimal. A reply that spends itself on the self channel
and ends with <|eot|> comes back with content '' (finish stop): the user sees nothing. This sends N chat requests
(training-side questions only: the night's own consolidated keys in a training wording, and the base anchor
prompts; never held-out or never-told probes), greedy, cache off, with the given lora scales, and counts replies
with non-empty content and finish "stop". The night fails when the rate is under --min-rate.

  eval/channel_check.py --server http://127.0.0.1:18620 --world W --day N --lora 0:1.0 --out FILE
"""

import argparse
import json
import random
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "night"))
from paths import runs_root  # noqa: E402

QUESTION = "Remind me, my {key}?"


def post(url, body, timeout=300):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def questions(runs, world, day, n_facts, n_general, seed, consolidated=None, anchors=None):
    consolidated = Path(consolidated) if consolidated else runs / world / str(day) / "night" / "consolidated.json"
    items = [i for i in json.loads(consolidated.read_text())["items"] if i["kind"] == "fact"]
    rng = random.Random(seed)
    qs = [{"source": f"key:{i['id']}", "question": QUESTION.format(key=i["key"].replace("_", " "))}
          for i in rng.sample(items, min(n_facts, len(items)))]
    anchors = Path(anchors) if anchors else next((runs / "_anchors").glob("*/anchors.jsonl"))
    pool = [json.loads(line) for line in anchors.read_text().splitlines() if line.strip()]
    qs += [{"source": f"anchor:{a['item_id']}", "question": a["user"]} for a in rng.sample(pool, min(n_general, len(pool)))]
    return qs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True, help="llama-server root URL (no /v1)")
    ap.add_argument("--world", required=True)
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--lora", required=True, help="the per-request lora scales as id:scale,id:scale (every loaded id)")
    ap.add_argument("--id-slot", type=int, help="pin a slot (the day server's slot 1)")
    ap.add_argument("--consolidated", help="default runs/<w>/<d>/night/consolidated.json")
    ap.add_argument("--anchors", help="default runs/_anchors/*/anchors.jsonl")
    ap.add_argument("--runs")
    ap.add_argument("--n-facts", type=int, default=20)
    ap.add_argument("--n-general", type=int, default=10)
    ap.add_argument("--max-tokens", type=int, default=256)
    ap.add_argument("--min-rate", type=float, default=0.95)
    ap.add_argument("--parallel", type=int, default=1)
    ap.add_argument("--seed", type=int, default=20261029)
    ap.add_argument("--label", default="")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    lora = [{"id": int(i), "scale": float(v)} for i, v in (pair.split(":") for pair in args.lora.split(","))]
    qs = questions(runs_root(args.runs), args.world, args.day, args.n_facts, args.n_general, args.seed, args.consolidated, args.anchors)

    def one(q):
        body = {"model": "muse-glimmer-30b", "messages": [{"role": "user", "content": q["question"]}], "max_tokens": args.max_tokens,
                "temperature": 0.0, "seed": 20261014, "cache_prompt": False, "lora": lora,
                "chat_template_kwargs": {"reasoning_strength": "minimal"}, **({"id_slot": args.id_slot} if args.id_slot is not None else {})}
        r = post(f"{args.server.rstrip('/')}/v1/chat/completions", body)
        c = r["choices"][0]
        content = (c["message"].get("content") or "").strip()
        ok = bool(content) and c.get("finish_reason") == "stop"
        row = {**q, "finish": c.get("finish_reason"), "content": content[:200],
               "reasoning": (c["message"].get("reasoning_content") or "")[:200], "ok": ok}
        if not ok:  # the app's one retry (app/afterglow/llm.py answer_channel): same prompt, answer channel forced
            rendered = post(f"{args.server.rstrip('/')}/apply-template", {"messages": body["messages"],
                                                                          "chat_template_kwargs": body["chat_template_kwargs"]})["prompt"]
            fix = post(f"{args.server.rstrip('/')}/completion", {
                "prompt": rendered.removesuffix("<|start|>assistant") + "<|start|>assistant to=user<|message|>",
                "n_predict": 320, "temperature": 0.0, "top_k": 1, "seed": 20261014, "cache_prompt": False, "lora": lora,
                **({"id_slot": args.id_slot} if args.id_slot is not None else {})})
            text = re.sub(r"(<\|(eot|end|return)\|>\s*)+$", "", (fix.get("content") or "").strip()).strip()
            row["retry_content"] = text[:200]
        row["ok_after_retry"] = ok or bool(row.get("retry_content"))
        return row

    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        rows = list(pool.map(one, qs))
    ok = sum(r["ok"] for r in rows)
    ok_retry = sum(r["ok_after_retry"] for r in rows)
    rec = {"label": args.label, "world": args.world, "day": args.day, "lora": lora, "n": len(rows), "content_ok": ok,
           "rate": round(ok / len(rows), 4) if rows else None, "min_rate": args.min_rate,
           "content_ok_after_retry": ok_retry, "retry": "the app's one answer-channel retry, temperature 0 (report only)",
           "passed": bool(rows) and ok / len(rows) >= args.min_rate,
           "rule": "non-empty content and finish 'stop' on chat-endpoint calls at reasoning_strength minimal", "replies": rows}
    Path(args.out).write_text(json.dumps(rec, indent=1) + "\n")
    print(json.dumps({k: rec[k] for k in ("label", "world", "n", "content_ok", "content_ok_after_retry", "rate", "passed")}))


if __name__ == "__main__":
    main()
