#!/usr/bin/env python3
"""Agent anchor rows: the base model's own replies to day-agent turns (its system prompt and tool list), kept only
when they take the action the turn was written for, so a night update keeps calling the tools.

The facts rows hold no tool turn, and R2 adapters skip recall on chat calls (w1, w3: 4/10). Each synthetic turn
carries its intended action from code:
  remember  - the user states a new work fact (made up here, about no world)
  recall    - the user asks about one of their own stored facts (the night's consolidated keys, minus the keys
              the tool-call check uses; and generic keys)
  calendar  - list a date's events / add an event;  tasks - list / add;  answer - a plain reply, no tool
The day server renders the prompt (/apply-template, the agent's reasoning level) and the base model (every adapter
at scale 0, slot 1, greedy, cache off) completes it (/completion). A reply is kept when its first recipient after
the self channel is the intended one and its arguments parse and hold the turn's value; drops are counted. Rows
carry the raw prompt and completion, trained verbatim (tool-call header included); none shares a key or a wording
with the tool-call check's turns.
"""

import argparse
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
from teacher import serving_base_url  # noqa: E402
import tool_call_check  # noqa: E402

FACTS = [("bike shed code", "4471"), ("plotter name", "Heron"), ("print queue name", "Q-West"), ("loading bay", "bay 6"),
         ("freight broker", "Lina Ostrom"), ("cloud bucket", "ops-archive-2"), ("training room", "room 0.12"),
         ("night guard", "Tomas Ek"), ("sample courier", "RapidLink"), ("visitor pass code", "VP-718"),
         ("fleet van", "van 3"), ("license server", "lic-02"), ("review board day", "Fridays"), ("badge office", "desk 9"),
         ("shared calendar name", "Ops Board")]
STATE = ["Heads up: the {key} is {value} from today.", "Small update for you, our {key} is {value}.",
         "Please keep in mind that the {key} is {value}."]
ASK = ["Do you still have what I said about my {key}?", "Can you look up my {key} for me?", "What was it I said my {key} is?",
       "I told you my {key} earlier, what was it?"]
GENERIC_RECALL = ["parking pass", "locker number", "desk phone", "mailbox code", "storage cage"]
CALENDAR_LIST = ["What do I have on {date}?", "Anything on my calendar for {date}?"]
CALENDAR_ADD = [("Put the vendor walkthrough on {date} at {time} in my calendar.", "vendor walkthrough"),
                ("Book the budget sync for {date} at {time}.", "budget sync")]
TASKS_LIST = ["Which tasks are still open?", "Show me my open tasks."]
TASKS_ADD = [("Add a task to send the invoice batch by {date}.", "invoice batch"), ("New to-do: order spare filters.", "spare filters")]
ANSWER = ["Thanks, that helps.", "Great, talk later.", "Okay, noted on my side.", "Cheers for the help today.",
          "That's everything for now."]
DATES = ["2026-11-04", "2026-11-09", "2026-11-13", "2026-11-17", "2026-11-25"]
TIMES = ["09:00", "11:30", "14:00", "16:15"]
TOOL_CHECK_SEED = 20261029  # eval/tool_call_check.py --seed default: its turns are the ones to stay clear of


def post(url, body, timeout=300):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def build_turns(items, exclude_keys, n, rng):
    keys = [i["key"].replace("_", " ") for i in items if i["kind"] == "fact" and i["key"].replace("_", " ").lower() not in exclude_keys]
    rng.shuffle(keys)
    t = []
    for k, (key, value) in enumerate(rng.sample(FACTS, 12)):
        t.append({"action": "remember", "user": STATE[k % 3].format(key=key, value=value), "value": value})
    for k, key in enumerate(keys[:15] + GENERIC_RECALL):
        t.append({"action": "recall", "user": ASK[k % 4].format(key=key), "key": key})
    for k in range(4):
        t.append({"action": "calendar.list_events", "user": CALENDAR_LIST[k % 2].format(date=DATES[k]), "value": DATES[k]})
        text, title = CALENDAR_ADD[k % 2]
        t.append({"action": "calendar.add_event", "user": text.format(date=DATES[k + 1], time=TIMES[k]), "value": title})
        t.append({"action": "tasks.list", "user": TASKS_LIST[k % 2]})
        text, title = TASKS_ADD[k % 2]
        t.append({"action": "tasks.add", "user": text.format(date=DATES[k]), "value": title})
    t += [{"action": "answer", "user": a} for a in ANSWER]
    return t[:n]


def first_action(completion):
    """The first recipient after the self channel, and its parameters."""
    heads = re.findall(r"to=([\w.]+)<\|message\|>", completion)
    target = next((h for h in heads if h != "self"), None)
    params = dict(re.findall(r'<atem:parameter name="(\w+)">(.*?)</atem:parameter>', completion, re.S))
    invoke = re.search(r'<atem:invoke name="([\w.]+)">', completion)
    return target, (invoke.group(1) if invoke else None), params


def kept(turn, completion):
    target, invoke, params = first_action(completion)
    a = turn["action"]
    if a == "answer":
        return target == "user" and "<atem:function_calls>" not in completion
    if target != a or invoke != a:
        return False
    if a == "remember":
        return bool(params.get("key")) and turn["value"].lower() in params.get("value", "").lower()
    if a == "recall":
        return bool(params.get("query", "").strip())
    if a == "calendar.list_events":
        return params.get("date") == turn["value"]
    if a == "calendar.add_event":
        return all(params.get(k) for k in ("date", "time", "title"))
    if a == "tasks.add":
        return turn["value"].split()[0] in params.get("title", "").lower()
    return True  # tasks.list


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", required=True)
    ap.add_argument("--consolidated", required=True)
    ap.add_argument("--server", help="root URL (default: the serving box in app/serve/hosts.json)")
    ap.add_argument("--n-loras", type=int, help="adapters loaded on the server, all sent at scale 0 (default: the server's /lora-adapters)")
    ap.add_argument("--reasoning", default="low", help="the day agent's default")
    ap.add_argument("--turns", type=int, default=60)
    ap.add_argument("--seed", type=int, default=20261029)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()
    root = (args.server or serving_base_url()).rstrip("/")
    root = root[:-3] if root.endswith("/v1") else root
    items = json.loads(Path(args.consolidated).read_text())["items"]
    check_turns = tool_call_check.turns(args.world, args.consolidated, TOOL_CHECK_SEED)
    check_ids = {t["item"] for t in check_turns if t["kind"] == "recall"}
    check_keys = {i["key"].replace("_", " ").lower() for i in items if i["id"] in check_ids}
    check_keys |= {k.lower() for k, _ in tool_call_check.REMEMBER}
    turns = build_turns(items, check_keys, args.turns, random.Random(args.seed))
    overlap = [t["user"] for t in turns if any(c["user"].lower() in t["user"].lower() or t["user"].lower() in c["user"].lower()
                                              for c in check_turns) or t.get("key", "").lower() in check_keys]
    if overlap:
        raise SystemExit(f"agent anchors share text or keys with the tool-call check: {overlap[:3]}")
    system = system_message(json.loads((ROOT / "worlds" / args.world / "world.json").read_text()))
    if args.n_loras is None:
        with urllib.request.urlopen(f"{root}/lora-adapters", timeout=30) as resp:
            args.n_loras = len(json.loads(resp.read()))
    lora = [{"id": i, "scale": 0.0} for i in range(args.n_loras)]
    rows, report = [], []
    for n, t in enumerate(turns):
        prompt = post(f"{root}/apply-template", {"messages": [system, {"role": "user", "content": t["user"]}], "tools": TOOL_SCHEMAS,
                                                 "chat_template_kwargs": {"reasoning_strength": args.reasoning}})["prompt"]
        if not prompt.endswith("<|start|>assistant"):
            raise SystemExit(f"unexpected rendered prompt end {prompt[-40:]!r}: stop")
        r = post(f"{root}/completion", {"prompt": prompt, "n_predict": 400, "temperature": 0.0, "top_k": 1, "seed": 20261014,
                                        "id_slot": 1, "cache_prompt": False, "lora": lora, "return_tokens": True})
        tokens = r.get("tokens") or []
        ended = bool(tokens) and tokens[-1] == 200008  # <|eot|>
        # the exact text of the generated tokens, special tokens included: "content" drops <|eom|> (200007), which ends
        # the self message before a tool call; a row without it teaches the adapter a reply the server cannot parse
        completion = post(f"{root}/detokenize", {"tokens": tokens, "with_pieces": False})["content"] if tokens else ""
        if ended and not completion.endswith("<|eot|>"):
            completion += "<|eot|>"
        # guard: the text must re-tokenize to exactly the generated ids (special tokens included)
        retok = post(f"{root}/tokenize", {"content": completion, "add_special": False, "parse_special": True})["tokens"] if tokens else []
        exact = retok == tokens
        ok = ended and r.get("stop_type") == "eos" and exact and kept(t, completion)
        report.append({**t, "kept": ok, "completion": completion[-300:], "tokens": r.get("tokens_predicted"), "prompt_tokens": r.get("tokens_evaluated"),
                       "eom": 200007 in tokens, "exact_tokens": exact})
        if ok:
            rows.append({"item_id": f"agent-{n:03d}", "kind": "agent_anchor", "form": f"agent_{t['action'].split('.')[0]}",
                         "user": t["user"], "assistant": completion, "raw": {"prompt": prompt[:-len("<|start|>assistant")],
                                                                              "completion": "<|start|>assistant" + completion,
                                                                              "completion_ids": tokens},
                         "valid": True})
    Path(args.out).write_text("".join(json.dumps(r) + "\n" for r in rows))
    by_action = {}
    for r in report:
        a = by_action.setdefault(r["action"], {"turns": 0, "kept": 0})
        a["turns"] += 1
        a["kept"] += r["kept"]
    summary = {"world": args.world, "turns": len(report), "kept": len(rows), "dropped": len(report) - len(rows), "by_action": by_action,
               "inexact_tokens": sum(not r["exact_tokens"] for r in report),
               "reasoning": args.reasoning, "tool_check_overlap": 0,
               "tokens": {"prompt": sum(r["prompt_tokens"] or 0 for r in report), "completion": sum(r["tokens"] or 0 for r in report)}}
    Path(args.report).write_text(json.dumps({**summary, "turns_detail": report}, indent=1) + "\n")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
