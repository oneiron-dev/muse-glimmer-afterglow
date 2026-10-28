#!/usr/bin/env python3
"""Raw replies on the tool-call check's turns: what the model writes after "<|start|>assistant" when the day
agent's prompt and tools are rendered (no tool-call parsing), adapter off and on. Diagnosis for a failed
tool_calls check: direct answer to the user, a self message that ends the turn, a wrong recipient, or a malformed call.

  eval/tool_turn_raw.py --server http://127.0.0.1:18620 --world W --consolidated FILE --out FILE
"""

import argparse
import collections
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "eval"))
from afterglow.agent import system_message  # noqa: E402
from afterglow.tools import TOOL_SCHEMAS  # noqa: E402
import tool_call_check  # noqa: E402


def post(url, body, timeout=300):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def shape(raw):
    heads = re.findall(r"to=([\w.]+)<\|message\|>", raw)
    if not heads:
        return "no header"
    first_other = next((h for h in heads if h != "self"), None)
    if first_other is None:
        return "self only, turn ended"
    if first_other == "user":
        return "answer to user" + (" after self" if heads[0] == "self" else " directly")
    return f"tool {first_other}" + ("" if "<atem:function_calls>" in raw else " (no call body)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True)
    ap.add_argument("--world", required=True)
    ap.add_argument("--consolidated", required=True)
    ap.add_argument("--reasoning", default="low")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    system = system_message(json.loads((ROOT / "worlds" / args.world / "world.json").read_text()))
    rows = []
    for t in tool_call_check.turns(args.world, args.consolidated, 20261029):
        prompt = post(f"{args.server}/apply-template", {"messages": [system, {"role": "user", "content": t["user"]}], "tools": TOOL_SCHEMAS,
                                                      "chat_template_kwargs": {"reasoning_strength": args.reasoning}})["prompt"]
        for arm, scale in (("base", 0.0), ("adapter", 1.0)):
            r = post(f"{args.server}/completion", {"prompt": prompt, "n_predict": 300, "temperature": 0.0, "top_k": 1, "seed": 20261014,
                                                   "cache_prompt": False, "lora": [{"id": 0, "scale": scale}], "n_probs": 3})
            first = (r.get("completion_probabilities") or [{}])[:4]
            rows.append({"arm": arm, "kind": t["kind"], "user": t["user"], "raw": r.get("content", "")[:600], "shape": shape(r.get("content", "")),
                         "first_tokens": [[c.get("token"), [[x.get("token"), round(x.get("logprob", 0), 3)] for x in c.get("top_logprobs", [])]] for c in first]})
    summary = {arm: dict(collections.Counter(r["shape"] for r in rows if r["arm"] == arm)) for arm in ("base", "adapter")}
    Path(args.out).write_text(json.dumps({"summary": summary, "rows": rows}, indent=1) + "\n")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
