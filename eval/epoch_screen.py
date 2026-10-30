#!/usr/bin/env python3
"""Epoch screen (lead 30 Oct 06:54, option B): one llama-server with the base and the night's epoch LoRAs (all loaded at
scale 0, one switched on per request), each arm scored on
  (a) facts, notes off: the day's forward and stale probes, asked as the night's eval asks them (arm B's prompt:
      reasoning minimal, the answer forced into the user channel, greedy), scored by eval/scoring.py;
  (b) known slices v1 + v2 in the day agent's form: its system prompt and tools (search_web not offered), reasoning
      low, greedy, 400 tokens; the reply in the user channel is graded by gold match (normalised, any gold alias
      inside the reply); a reply that declines (omniscience.says_abstain) is counted as declined. When the first action
      is recall calls only, the product runs them and, on a general question, finds nothing: the calls (the server's
      chat parse) and an empty result {"notes": []} per call go into the prompt and the second turn is graded (lead
      30 Oct 11:16; counted as "recalled"). Any other first tool call is counted as "tool".
Deterministic: no grader model. Each arm is compared with the base asked in the same server run.
Faster, same measure (lead 30 Oct 10:11): --reuse-facts arm=score-file takes an arm's facts from the night's eval, which
asks the same probes in the same form (the base = arm A, the epoch of record = arm B); --base-cache keeps the base's
known-slice completions per world (same server build, prompt and decoding every night), so later nights ask only new ones.
Usage: python3 eval/epoch_screen.py --server http://HOST:PORT --world W --day D --arms base=-1,epoch2=0,epoch3=1,epoch4=2 --out F
       [--reuse-facts base=eval/score-night-A.json,epoch4=eval/score-night-B.json] [--base-cache runs/W/_screen_cache]
       (arm=LoRA id on the server, -1 = the base: every adapter at 0)
"""

import argparse
import hashlib
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


def known_answer(server, system, question, prompt, lora_list):
    """(completion, cut, recalled): the reply the product gets; a recall-only first action gets its second turn."""
    comp, cut = complete(server, prompt, 400, lora_list)
    if cut or set(re.findall(r'<atem:invoke name="([\w.]+)">', comp)) != {"recall"}:
        return comp, cut, False
    messages = [system, {"role": "user", "content": question}]
    msg = post(server, "/v1/chat/completions", {"messages": messages, "tools": TOOL_SCHEMAS, "max_tokens": 400, "temperature": 0.0,
                                                "top_k": 1, "seed": 20261014, "cache_prompt": False, "lora": lora_list,
                                                "chat_template_kwargs": {"reasoning_strength": "low"}})["choices"][0]["message"]
    calls = msg.get("tool_calls") or []
    if not calls or any(c["function"]["name"] != "recall" for c in calls):
        return comp, cut, False
    msg = {"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls,
           **({"reasoning_content": msg["reasoning_content"]} if msg.get("reasoning_content") else {})}
    tools = [{"role": "tool", "tool_call_id": c.get("id") or f"call_{i}", "name": "recall", "content": json.dumps({"notes": []})}
             for i, c in enumerate(calls)]
    p2 = post(server, "/apply-template", {"messages": messages + [msg] + tools, "tools": TOOL_SCHEMAS,
                                          "chat_template_kwargs": {"reasoning_strength": "low"}})["prompt"]
    comp2, cut2 = complete(server, p2, 400, lora_list)
    return comp2, cut2, True


def agent_reply(completion):
    """(outcome, reply): the first action after the self channel."""
    heads = re.findall(r"to=([\w.]+)<\|message\|>", completion)
    target = next((h for h in heads if h != "self"), None)
    if target and target != "user":
        return "tool", ""
    reply = completion.split("to=user<|message|>")[-1] if "to=user<|message|>" in completion else completion
    return "reply", re.split(r"<\|(eot|end|return|call)\|>", reply)[0]


def facts_from_eval(path, probes):
    """An arm's facts from the night's eval score file (the same probes, prompt and decoding); scored as below."""
    got = {r["id"]: r for r in json.loads(Path(path).read_text())["results"] if r["kind"] in FACT_KINDS}
    missing = [p["id"] for p in probes if p["id"] not in got]
    if missing:
        raise SystemExit(f"{path} lacks {len(missing)} probes ({missing[:3]})")
    return [(got[p["id"]]["answer"], bool(got[p["id"]].get("truncated"))) for p in probes]


def score_facts(probes, fg, facts_by_id):
    fr = {"forward": {"right": 0, "wrong": 0, "abstain": 0}, "stale": {"returned": 0, "n": 0}}
    for p, (ans, cut) in zip(probes, fg):
        passed, _ = scoring.score_probe(p, scoring.strip_answer(ans), cut, facts_by_id)
        if p["kind"] == "stale":
            fr["stale"]["n"] += 1
            fr["stale"]["returned"] += bool(passed)
        else:
            fr["forward"]["right" if passed else "abstain" if says_abstain(ans) else "wrong"] += 1
    return fr


def base_cache_key(build, prompt):
    return hashlib.sha256(json.dumps([build, prompt, 400, "greedy", 20261014, "recall-second-turn"]).encode()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True)
    ap.add_argument("--world", required=True)
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--arms", required=True, help="name=lora_id,... (-1 = base)")
    ap.add_argument("--known", default="known_slice_v1,known_slice_v2")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--limit", type=int, help="first N items of each set (a smoke run)")
    ap.add_argument("--reuse-facts", default="", help="arm=eval score file,... (the facts from the night's eval)")
    ap.add_argument("--base-cache", help="directory of the per-world cache of the base's known completions")
    ap.add_argument("--known-only", action="store_true", help="the known slices only (a re-score; facts not asked)")
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
    probes = [] if args.known_only else probes
    known = [{**k, "slice": name} for name in args.known.split(",") for k in read_jsonl(ROOT / "eval" / "data" / f"{name}.jsonl")[:args.limit]]
    system = system_message(world)
    fact_prompts = [fact_prompt(server, prompt_text(p, None, first)) for p in probes]
    known_prompts = [agent_prompt(server, system, k["question"]) for k in known]
    out = {"world": args.world, "day": args.day, "server_lora_adapters": loaded, "arms": {}, "rule": __doc__.split("\n\n")[0]}
    reuse = dict(a.split("=", 1) for a in args.reuse_facts.split(",") if a)
    cache_path, cache, build = None, {}, None
    if args.base_cache:
        with urllib.request.urlopen(server + "/props", timeout=30) as resp:
            props = json.loads(resp.read())
        build = [props.get("build_info"), Path(props.get("model_path") or "").name]
        cache_path = Path(args.base_cache) / f"{args.world}-base-known.jsonl"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        if cache_path.exists():
            cache = {r["key"]: (r["completion"], r["cut"], r.get("recalled", False))
                     for r in map(json.loads, cache_path.read_text().splitlines()) if r}
    answers, how = {}, {}
    for name, arm_id in arms.items():
        lo = lora(len(loaded), arm_id)
        cached = bool(cache_path) and arm_id == -1
        keys = [base_cache_key(build, pr) for pr in known_prompts] if cached else []
        todo = [i for i, k in enumerate(keys) if k not in cache] if cached else list(range(len(known_prompts)))
        with ThreadPoolExecutor(max_workers=args.parallel) as pool:
            fg = (facts_from_eval(reuse[name], probes) if name in reuse else
                  list(pool.map(lambda pr: complete(server, pr[1], MAX_NEW_TOKENS.get(pr[0]["kind"], MAX_NEW_TOKENS["general"]), lo), zip(probes, fact_prompts))))
            new = dict(zip(todo, pool.map(lambda i: known_answer(server, system, known[i]["question"], known_prompts[i], lo), todo)))
        if cached:
            with open(cache_path, "a") as f:
                for i, (comp, cut, rec) in new.items():
                    cache[keys[i]] = (comp, cut, rec)
                    f.write(json.dumps({"key": keys[i], "id": known[i]["id"], "completion": comp, "cut": cut, "recalled": rec}) + "\n")
            kg = [cache[k] for k in keys]
        else:
            kg = [new[i] for i in range(len(known_prompts))]
        how[name] = {"facts": f"from {reuse[name]}" if name in reuse else "asked",
                     "known": f"asked {len(todo)}, cached {len(known_prompts) - len(todo)}"}
        fr = score_facts(probes, fg, facts_by_id) if probes else None
        ks, rows = {}, []
        for k, (comp, cut, rec) in zip(known, kg):
            outcome, reply = agent_reply(comp)
            right = outcome == "reply" and not cut and gold_match(reply, k["gold"])
            s = ks.setdefault(k["slice"], {"n": 0, "right": 0, "declined": 0, "recalled": 0, "tool": 0, "cut": 0})
            s["n"] += 1
            s["recalled"] += rec
            s["right"] += right
            s["declined"] += outcome == "reply" and not cut and says_abstain(reply)
            s["tool"] += outcome == "tool"
            s["cut"] += cut
            rows.append({"id": k["id"], "right": right, "outcome": outcome, "recalled": rec, "reply": reply[:200]})
        answers[name] = rows
        out["arms"][name] = {"lora_id": arm_id, "facts_notes_off": fr, "known": ks}
        print(json.dumps({"arm": name, **out["arms"][name]}), flush=True)
    if "base" in answers:
        base = {r["id"]: r["right"] for r in answers["base"]}
        for name, rows in answers.items():
            if name != "base":
                out["arms"][name]["known_vs_base"] = {"right_to_wrong": sum(1 for r in rows if base.get(r["id"]) and not r["right"]),
                                                      "wrong_to_right": sum(1 for r in rows if not base.get(r["id"]) and r["right"])}
    out["how"] = how
    out["answers"] = answers
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()
