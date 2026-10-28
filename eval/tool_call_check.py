#!/usr/bin/env python3
"""Tool-call check: does the adapter still call the day agent's tools when the app calls it?

The night trains the answer header (" to=user"), which could teach the adapter never to address a tool. This sends
20 agent turns through /v1/chat/completions exactly as the day agent does (its system prompt, its tool list, its
reasoning level), greedy, cache off, with the given lora scales, and scores the FIRST reply:
  remember turns (10) - the user states a new work fact (made up here, about no world): right when the reply calls
                        remember with string key and value, and the value holds the fact's value
  recall turns (10)   - the user asks about one of the night's own consolidated keys in a training wording: right
                        when the reply calls recall with a string query
No held-out or never-told probe is used. Run once with the adapter off and once on; the night fails when the
adapter's count is more than --max-drop below the base's.

  eval/tool_call_check.py --server URL --world W --day N --lora 0:1.0 --base-lora 0:0.0 --out FILE
"""

import argparse
import json
import random
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "night"))
from afterglow.agent import system_message  # noqa: E402
from afterglow.tools import TOOL_SCHEMAS  # noqa: E402
from paths import runs_root  # noqa: E402

# New work facts for the remember turns (generic, about no synthetic world).
REMEMBER = [("label printer name", "Puffin"), ("build server name", "Kestrel-07"), ("backup drive name", "Ironwood"),
            ("courier account number", "CX-55120"), ("quarterly review room", "Atrium B"), ("VPN username", "ops.lumen"),
            ("vendor portal code", "VP-3381"), ("storage unit number", "unit 14C"), ("team offsite date", "2026-11-20"),
            ("invoice prefix", "INV-HV-"), ("spare key holder", "Rhea Lund"), ("printer room", "room 1.09")]
REMEMBER_WORDING = ["Quick note: my {key} is {value}.", "For the record, the {key} is {value} now.",
                    "Write this down: {key} {value}."]
RECALL_WORDING = ["What did I tell you about my {key}?", "Remind me what my {key} was.", "Do you have my {key} somewhere?"]


def post(url, body, timeout=300):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def lora_list(text):
    return [{"id": int(i), "scale": float(v)} for i, v in (pair.split(":") for pair in text.split(","))]


def turns(world, consolidated, seed):
    rng = random.Random(seed)
    items = [i for i in json.loads(Path(consolidated).read_text())["items"] if i["kind"] == "fact"]
    out = [{"kind": "remember", "user": REMEMBER_WORDING[n % 3].format(key=k, value=v), "value": v}
           for n, (k, v) in enumerate(rng.sample(REMEMBER, 10))]
    out += [{"kind": "recall", "user": RECALL_WORDING[n % 3].format(key=i["key"].replace("_", " ")), "item": i["id"]}
            for n, i in enumerate(rng.sample(items, min(10, len(items))))]
    return out


def score(turn, msg):
    calls = msg.get("tool_calls") or []
    if not calls:
        return False, "no tool call"
    fn = calls[0]["function"]
    try:
        args = json.loads(fn.get("arguments") or "{}")
    except ValueError:
        return False, f"{fn.get('name')}: arguments not JSON"
    if turn["kind"] == "remember":
        ok = fn.get("name") == "remember" and isinstance(args, dict) and isinstance(args.get("key"), str) \
            and isinstance(args.get("value"), str) and turn["value"].lower() in args["value"].lower()
        return ok, f"{fn.get('name')} {json.dumps(args)[:120]}"
    ok = any(c["function"].get("name") == "recall" for c in calls) and isinstance(args, dict) and isinstance(args.get("query"), str)
    return ok, f"{fn.get('name')} {json.dumps(args)[:120]}"


def run_arm(args, lora, ts, system):
    def one(t):
        body = {"model": "muse-glimmer-30b", "messages": [system, {"role": "user", "content": t["user"]}], "tools": TOOL_SCHEMAS,
                "max_tokens": 512, "temperature": 0.0, "seed": 20261014, "cache_prompt": False, "lora": lora,
                "chat_template_kwargs": {"reasoning_strength": args.reasoning},
                **({"id_slot": args.id_slot} if args.id_slot is not None else {})}
        choice = post(f"{args.server.rstrip('/')}/v1/chat/completions", body)["choices"][0]
        msg = choice["message"]
        ok, note = score(t, msg)
        return {**t, "ok": ok, "note": note, "content": (msg.get("content") or "")[:120], "finish": choice.get("finish_reason"),
                "reasoning": (msg.get("reasoning_content") or "")[:300]}

    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        return list(pool.map(one, ts))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True, help="llama-server root URL (no /v1)")
    ap.add_argument("--world", required=True)
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--lora", required=True, help="adapter on: id:scale,id:scale (every loaded id)")
    ap.add_argument("--base-lora", required=True, help="adapter off: id:scale,...")
    ap.add_argument("--consolidated", help="default runs/<w>/<d>/night/consolidated.json")
    ap.add_argument("--runs")
    ap.add_argument("--reasoning", default="low", help="the day agent's default")
    ap.add_argument("--id-slot", type=int)
    ap.add_argument("--max-drop", type=int, default=2)
    ap.add_argument("--parallel", type=int, default=1)
    ap.add_argument("--seed", type=int, default=20261029)
    ap.add_argument("--label", default="")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    consolidated = args.consolidated or runs_root(args.runs) / args.world / str(args.day) / "night" / "consolidated.json"
    world_doc = json.loads((ROOT / "worlds" / args.world / "world.json").read_text())
    system = system_message(world_doc)
    ts = turns(args.world, consolidated, args.seed)
    arms = {"base": run_arm(args, lora_list(args.base_lora), ts, system), "adapter": run_arm(args, lora_list(args.lora), ts, system)}
    counts = {a: {"remember": sum(r["ok"] for r in rows if r["kind"] == "remember"),
                  "recall": sum(r["ok"] for r in rows if r["kind"] == "recall"), "n": len(rows),
                  "right": sum(r["ok"] for r in rows)} for a, rows in arms.items()}
    rec = {"label": args.label, "world": args.world, "day": args.day, "reasoning": args.reasoning, "counts": counts,
           "max_drop": args.max_drop, "passed": counts["adapter"]["right"] >= counts["base"]["right"] - args.max_drop,
           "rule": "adapter right >= base right - max_drop (first reply calls the expected tool with valid arguments)",
           "turns": arms}
    Path(args.out).write_text(json.dumps(rec, indent=1) + "\n")
    print(json.dumps({k: rec[k] for k in ("label", "world", "counts", "passed")}))


if __name__ == "__main__":
    main()
