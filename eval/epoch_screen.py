#!/usr/bin/env python3
"""Epoch screen (lead 30 Oct 06:54, option B): one llama-server with the base and the night's epoch LoRAs (all loaded at
scale 0, one switched on per request), each arm scored on
  (a) facts, notes off: the day's forward and stale probes, asked as the night's eval asks them (arm B's prompt:
      reasoning minimal, the answer forced into the user channel, greedy), scored by eval/scoring.py;
  (b) known slices v1 + v2 in the day agent's form: its system prompt and tools (search_web not offered), reasoning
      low, greedy, 400 tokens; the reply in the user channel is graded by gold match (normalised, any gold alias
      inside the reply). A first action that is a tool call is counted as "tool" (no second turn here).
Deterministic: no grader model. Each arm is compared with the base asked in the same server run.
Usage: python3 eval/epoch_screen.py --server http://HOST:PORT --world W --day D --arms base=-1,epoch2=0,epoch3=1,epoch4=2 --out F
       (arm=LoRA id on the server, -1 = the base: every adapter at 0)
"""

import argparse
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT / "app"))
import scoring  # noqa: E402
from omniscience import says_abstain  # noqa: E402
from score_peft import EVAL_DATE, KNOWLEDGE_CUTOFF, MAX_NEW_TOKENS, probes_for_day, prompt_text, read_jsonl  # noqa: E402
from afterglow.agent import system_message  # noqa: E402
from afterglow.tools import TOOL_SCHEMAS  # noqa: E402

ANSWER_PREFIX = "<|start|>assistant to=user<|message|>"
FACT_KINDS = ("forward", "stale")
ARTICLES = re.compile(r"\b(a|an|the)\b")


def post(server, path, body, timeout=600):
    req = urllib.request.Request(server + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def norm(text):
    t = (text or "").lower().replace("’", "'")
    t = re.sub(r"[^a-z0-9' ]+", " ", t)
    return " ".join(ARTICLES.sub(" ", t).split())


def gold_match(reply, golds):
    r = norm(reply)
    return any(norm(g) and norm(g) in r for g in golds)


def lora(n, arm_id):
    return [{"id": i, "scale": 1.0 if i == arm_id else 0.0} for i in range(n)]


def fact_prompt(server, text):
    p = post(server, "/apply-template", {"messages": [{"role": "user", "content": text}],
                                         "chat_template_kwargs": {"reasoning_strength": "minimal", "knowledge_cutoff": KNOWLEDGE_CUTOFF,
                                                                  "current_date": EVAL_DATE}})["prompt"]
    if not p.endswith("<|start|>assistant"):
        raise SystemExit(f"unexpected rendered prompt end {p[-40:]!r}")
    return p[:-len("<|start|>assistant")] + ANSWER_PREFIX


def agent_prompt(server, system, question):
    p = post(server, "/apply-template", {"messages": [system, {"role": "user", "content": question}], "tools": TOOL_SCHEMAS,
                                         "chat_template_kwargs": {"reasoning_strength": "low"}})["prompt"]
    if not p.endswith("<|start|>assistant"):
        raise SystemExit(f"unexpected rendered prompt end {p[-40:]!r}")
    return p


def complete(server, prompt, n_predict, lora_list):
    r = post(server, "/completion", {"prompt": prompt, "n_predict": n_predict, "temperature": 0.0, "top_k": 1, "seed": 20261014,
                                     "cache_prompt": False, "lora": lora_list})
    return r.get("content", ""), r.get("stop_type") == "limit"


def agent_reply(completion):
    """(outcome, reply): the first action after the self channel."""
    heads = re.findall(r"to=([\w.]+)<\|message\|>", completion)
    target = next((h for h in heads if h != "self"), None)
    if target and target != "user":
        return "tool", ""
    reply = completion.split("to=user<|message|>")[-1] if "to=user<|message|>" in completion else completion
    return "reply", re.split(r"<\|(eot|end|return|call)\|>", reply)[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True)
    ap.add_argument("--world", required=True)
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--arms", required=True, help="name=lora_id,... (-1 = base)")
    ap.add_argument("--known", default="known_slice_v1,known_slice_v2")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--limit", type=int, help="first N items of each set (a smoke run)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    server = args.server.rstrip("/")
    with urllib.request.urlopen(server + "/lora-adapters", timeout=30) as resp:
        loaded = json.loads(resp.read())
    arms = {k: int(v) for k, v in (a.split("=") for a in args.arms.split(","))}
    world = json.loads((ROOT / "worlds" / args.world / "world.json").read_text())
    facts_by_id = {f["id"]: f for f in world["facts"]}
    first = world["persona"]["name"].split()[0]
    probes = [p for p in probes_for_day(read_jsonl(ROOT / "worlds" / args.world / "heldout" / "probes.jsonl"), facts_by_id, args.day)
              if p["kind"] in FACT_KINDS][:args.limit]
    known = [{**k, "slice": name} for name in args.known.split(",") for k in read_jsonl(ROOT / "eval" / "data" / f"{name}.jsonl")[:args.limit]]
    system = system_message(world)
    fact_prompts = [fact_prompt(server, prompt_text(p, None, first)) for p in probes]
    known_prompts = [agent_prompt(server, system, k["question"]) for k in known]
    out = {"world": args.world, "day": args.day, "server_lora_adapters": loaded, "arms": {}, "rule": __doc__.split("\n\n")[0]}
    answers = {}
    for name, arm_id in arms.items():
        lo = lora(len(loaded), arm_id)
        with ThreadPoolExecutor(max_workers=args.parallel) as pool:
            fg = list(pool.map(lambda pr: complete(server, pr[1], MAX_NEW_TOKENS.get(pr[0]["kind"], MAX_NEW_TOKENS["general"]), lo), zip(probes, fact_prompts)))
            kg = list(pool.map(lambda pr: complete(server, pr, 400, lo), known_prompts))
        fr = {"forward": {"right": 0, "wrong": 0, "abstain": 0}, "stale": {"returned": 0, "n": 0}}
        for p, (ans, cut) in zip(probes, fg):
            passed, _ = scoring.score_probe(p, scoring.strip_answer(ans), cut, facts_by_id)
            if p["kind"] == "stale":
                fr["stale"]["n"] += 1
                fr["stale"]["returned"] += bool(passed)
            else:
                fr["forward"]["right" if passed else "abstain" if says_abstain(ans) else "wrong"] += 1
        ks, rows = {}, []
        for k, (comp, cut) in zip(known, kg):
            outcome, reply = agent_reply(comp)
            right = outcome == "reply" and not cut and gold_match(reply, k["gold"])
            s = ks.setdefault(k["slice"], {"n": 0, "right": 0, "tool": 0, "cut": 0})
            s["n"] += 1
            s["right"] += right
            s["tool"] += outcome == "tool"
            s["cut"] += cut
            rows.append({"id": k["id"], "right": right, "outcome": outcome, "reply": reply[:200]})
        answers[name] = rows
        out["arms"][name] = {"lora_id": arm_id, "facts_notes_off": fr, "known": ks}
        print(json.dumps({"arm": name, **out["arms"][name]}), flush=True)
    if "base" in answers:
        base = {r["id"]: r["right"] for r in answers["base"]}
        for name, rows in answers.items():
            if name != "base":
                out["arms"][name]["known_vs_base"] = {"right_to_wrong": sum(1 for r in rows if base.get(r["id"]) and not r["right"]),
                                                      "wrong_to_right": sum(1 for r in rows if not base.get(r["id"]) and r["right"])}
    out["answers"] = answers
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()
