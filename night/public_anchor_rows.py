#!/usr/bin/env python3
"""Public anchor rows (lead 30 Oct 01:35-01:38): the base model's own replies, in the day agent's form as served
(its system prompt and tools, search_web offered), to general-knowledge questions from the frozen public-anchor pool
(eval/data/public_anchor_pool_v1.jsonl). A reply is kept when it answers or calls search_web, and dropped when it
declines or asks the user back. The rows sit beside the ask-back rows, so the night learns "the user's own key,
never told -> ask" and "a public question -> answer or look it up" side by side. --rows N picks N kept replies
(N = the world-night's ask-back row count); the pool order per world is fixed by its seed. Rows are the raw prompt and
completion, trained verbatim like the agent anchors; the receipt carries the pool's hash.
"""

import argparse
import hashlib
import json
import random
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT / "night"))
from afterglow.agent import system_message  # noqa: E402
from afterglow.tools import TOOL_SCHEMAS  # noqa: E402
from afterglow.web_search import SEARCH_WEB_SCHEMA  # noqa: E402
from agent_anchor_rows import first_action, post  # noqa: E402
from omniscience import says_abstain  # noqa: E402
from teacher import serving_base_url  # noqa: E402

POOL = ROOT / "eval" / "data" / "public_anchor_pool_v1.jsonl"
ASKS_BACK = re.compile(r"can you tell me|could you tell me|what is it\?|haven't told me|have not told me|in your notes|"
                       r"in my notes|any notes|do you know", re.I)


def frozen_pool():
    want = json.loads(POOL.with_suffix(".manifest.json").read_text())["sha256"]
    got = hashlib.sha256(POOL.read_bytes()).hexdigest()
    if got != want:
        raise SystemExit(f"{POOL} sha256 {got} differs from its manifest ({want})")
    return [json.loads(line) for line in POOL.read_text().splitlines() if line.strip()], got


def verdict(completion):
    """(kept, how): answered in the user channel without declining or asking back, or a search_web call."""
    target, invoke, params = first_action(completion)
    if target == "search_web" or invoke == "search_web":
        return bool(params.get("query", "").strip()), "search"
    if target != "user" or "<atem:function_calls>" in completion:
        return False, f"other action ({target or invoke})"
    reply = completion.split("to=user<|message|>")[-1].replace("<|eot|>", "")
    if says_abstain(reply) or ASKS_BACK.search(reply):
        return False, "declined or asked back"
    return True, "answer"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", required=True)
    ap.add_argument("--rows", type=int, required=True)
    ap.add_argument("--server", help="root URL (default: the serving box in app/serve/hosts.json)")
    ap.add_argument("--n-loras", type=int)
    ap.add_argument("--reasoning", default="low", help="the day agent's default")
    ap.add_argument("--seed", type=int, default=20261030)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()
    root = (args.server or serving_base_url()).rstrip("/")
    root = root[:-3] if root.endswith("/v1") else root
    pool, pool_sha = frozen_pool()
    rng = random.Random(f"{args.seed}:{args.world}")
    order = pool[:]
    rng.shuffle(order)
    system = system_message(json.loads((ROOT / "worlds" / args.world / "world.json").read_text()))
    if args.n_loras is None:
        with urllib.request.urlopen(f"{root}/lora-adapters", timeout=30) as resp:
            args.n_loras = len(json.loads(resp.read()))
    lora = [{"id": i, "scale": 0.0} for i in range(args.n_loras)]
    tools = TOOL_SCHEMAS + [SEARCH_WEB_SCHEMA]
    rows, report = [], []
    for item in order:
        if len(rows) >= args.rows:
            break
        prompt = post(f"{root}/apply-template", {"messages": [system, {"role": "user", "content": item["question"]}], "tools": tools,
                                                 "chat_template_kwargs": {"reasoning_strength": args.reasoning}})["prompt"]
        if not prompt.endswith("<|start|>assistant"):
            raise SystemExit(f"unexpected rendered prompt end {prompt[-40:]!r}: stop")
        r = post(f"{root}/completion", {"prompt": prompt, "n_predict": 400, "temperature": 0.0, "top_k": 1, "seed": 20261014,
                                        "id_slot": 1, "cache_prompt": False, "lora": lora, "return_tokens": True})
        tokens = r.get("tokens") or []
        ended = bool(tokens) and tokens[-1] == 200008  # <|eot|>
        completion = post(f"{root}/detokenize", {"tokens": tokens, "with_pieces": False})["content"] if tokens else ""
        if ended and not completion.endswith("<|eot|>"):
            completion += "<|eot|>"
        retok = post(f"{root}/tokenize", {"content": completion, "add_special": False, "parse_special": True})["tokens"] if tokens else []
        exact = retok == tokens
        ok, how = verdict(completion) if ended and r.get("stop_type") == "eos" and exact else (False, "cut or inexact tokens")
        report.append({"id": item["id"], "part": item["part"], "question": item["question"], "kept": ok, "how": how,
                       "completion": completion[-300:], "tokens": r.get("tokens_predicted")})
        if ok:
            rows.append({"item_id": f"public-{item['id']}", "kind": "public_anchor", "form": f"public_{how}",
                         "user": item["question"], "assistant": completion,
                         "raw": {"prompt": prompt[:-len("<|start|>assistant")], "completion": "<|start|>assistant" + completion,
                                 "completion_ids": tokens}, "valid": True})
    Path(args.out).write_text("".join(json.dumps(r) + "\n" for r in rows))
    hows = {}
    for x in report:
        hows[x["how"]] = hows.get(x["how"], 0) + 1
    summary = {"world": args.world, "pool": str(POOL.relative_to(ROOT)), "pool_sha256": pool_sha, "asked": len(report),
               "kept": len(rows), "wanted": args.rows, "by_outcome": hows,
               "kept_by_part": {p: sum(1 for x in report if x["kept"] and x["part"] == p) for p in ("nq", "who_is", "where_is")}}
    Path(args.report).write_text(json.dumps({**summary, "asked_detail": report}, indent=1) + "\n")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
