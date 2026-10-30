#!/usr/bin/env python3
"""Soft-anchor rows (lead 30 Oct 08:03-08:05; research round 2 #1): base Glimmer's own replies, in the day agent's
form as served (its system prompt and tools, search_web not offered, reasoning low, greedy), to four prompt pools.
The night trains them with a top-20 KL term toward plain Glimmer (night/train_adapter.py --kl-weight), never with
cross-entropy:
  obscure  - candidates of eval/data/kl_pool_candidates_v1.jsonl part "obscure", kept where base declines;
  known    - part "known", kept where base's reply holds a gold answer (normalised match);
  people   - part "people", every reply in the user channel kept;
  swapped  - this night's own user-fact questions (rewrites, form qa_forward) with the user named as a public person
             ("my <key>" -> "<Name>'s <key>", the user's name -> <Name>); every finished reply kept (base's own
             behaviour on an unknown person, whatever it is).
The fixed pools (obscure, known, people) depend only on the world's system prompt and plain Glimmer, so they are made
once per world and cached (--cache-dir); the swapped pool is made every night. Rows are the raw prompt and completion
with the generated token ids, like the agent anchors. When base's first action is one recall call, the row is the
second turn: the call and the product's empty recall result are in the prompt (generate()).
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
from agent_anchor_rows import first_action, post  # noqa: E402
from omniscience import says_abstain  # noqa: E402

CANDIDATES = ROOT / "eval" / "data" / "kl_pool_candidates_v1.jsonl"
KEEP = {"obscure": 100, "known": 100, "people": 100}
ASKS_BACK = re.compile(r"can you tell me|could you tell me|what is it\?|haven't told me|have not told me|in your notes|"
                       r"in my notes|any notes|don't know|do not know|not sure|no information|unable to", re.I)
ARTICLES = re.compile(r"\b(a|an|the)\b")


def norm(text):
    t = re.sub(r"[^a-z0-9' ]+", " ", (text or "").lower().replace("’", "'"))
    return " ".join(ARTICLES.sub(" ", t).split())


def frozen_candidates():
    want = json.loads(CANDIDATES.with_suffix(".manifest.json").read_text())["sha256"]
    got = hashlib.sha256(CANDIDATES.read_bytes()).hexdigest()
    if got != want:
        raise SystemExit(f"{CANDIDATES} sha256 {got} differs from its manifest")
    return [json.loads(line) for line in CANDIDATES.read_text().splitlines() if line.strip()], got


GREEDY = {"temperature": 0.0, "top_k": 1, "seed": 20261014, "id_slot": 1, "cache_prompt": False}
N_PREDICT = 800
NOTHING_RECALLED = {"notes": []}  # the product's recall result when no note matches


def render(root, messages):
    prompt = post(f"{root}/apply-template", {"messages": messages, "tools": TOOL_SCHEMAS,
                                             "chat_template_kwargs": {"reasoning_strength": "low"}})["prompt"]
    if not prompt.endswith("<|start|>assistant"):
        raise SystemExit(f"unexpected rendered prompt end {prompt[-40:]!r}")
    return prompt


def complete(root, prompt, lora):
    r = post(f"{root}/completion", {"prompt": prompt, "n_predict": N_PREDICT, "lora": lora, "return_tokens": True, **GREEDY})
    tokens = r.get("tokens") or []
    ended = bool(tokens) and tokens[-1] == 200008  # <|eot|>
    completion = post(f"{root}/detokenize", {"tokens": tokens, "with_pieces": False})["content"] if tokens else ""
    if ended and not completion.endswith("<|eot|>"):
        completion += "<|eot|>"
    exact = tokens and post(f"{root}/tokenize", {"content": completion, "add_special": False, "parse_special": True})["tokens"] == tokens
    return completion, tokens, bool(ended and r.get("stop_type") == "eos" and exact)


def generate(root, system, question, lora):
    """Base's reply as the product gets it. When base's first action is recall calls only, the product runs them (a
    raw completion runs past <|eom|> into repeated recall calls; the chat request's parse is what the product gets);
    the question is not about the user, so each recall finds nothing: the calls and one NOTHING_RECALLED per call go
    into the prompt, and the row's completion is the second turn."""
    messages = [system, {"role": "user", "content": question}]
    prompt = render(root, messages)
    completion, tokens, ok = complete(root, prompt, lora)
    invokes = set(re.findall(r'<atem:invoke name="([\w.]+)">', completion))
    if not ok or invokes != {"recall"}:
        return prompt, completion, tokens, ok, False
    msg = post(f"{root}/v1/chat/completions", {"messages": messages, "tools": TOOL_SCHEMAS, "max_tokens": N_PREDICT,
                                               "chat_template_kwargs": {"reasoning_strength": "low"}, "lora": lora,
                                               **GREEDY})["choices"][0]["message"]
    calls = msg.get("tool_calls") or []
    if not calls or any(c["function"]["name"] != "recall" for c in calls):
        return prompt, completion, tokens, False, True
    msg = {"role": "assistant", "content": msg.get("content") or "", **({"reasoning_content": msg["reasoning_content"]}
           if msg.get("reasoning_content") else {}), "tool_calls": calls}  # the fields the product keeps (llm.py)
    tools = [{"role": "tool", "tool_call_id": c.get("id") or f"call_{i}", "name": "recall", "content": json.dumps(NOTHING_RECALLED)}
             for i, c in enumerate(calls)]
    prompt2 = render(root, messages + [msg] + tools)
    completion2, tokens2, ok2 = complete(root, prompt2, lora)
    return prompt2, completion2, tokens2, ok2, True


def outcome(completion):
    target, invoke, _ = first_action(completion)
    if target != "user" or "<atem:function_calls>" in completion:
        return "tool", ""
    return "reply", completion.split("to=user<|message|>")[-1].replace("<|eot|>", "")


def keep(item, completion):
    kind, reply = outcome(completion)
    if item["part"] == "swapped":
        return True, kind
    if kind != "reply":
        return False, "tool"
    declined = says_abstain(reply) or bool(ASKS_BACK.search(reply))
    if item["part"] == "obscure":
        return declined, "declined" if declined else "answered"
    if item["part"] == "known":
        right = any(norm(g) and norm(g) in norm(reply) for g in item["gold"])
        return right and not declined, "right" if right else "not right"
    return True, "reply"  # people


def swapped_questions(rewrites, user_name, public_names, n, rng):
    """The night's user-fact questions with the user named as a public person."""
    first = user_name.split()[0]
    qs, seen = [], set()
    rows = [json.loads(line) for line in Path(rewrites).read_text().splitlines() if line.strip()]
    for r in rows:
        if r.get("form") != "qa_forward" or r.get("item_id") in seen:
            continue
        q = r["messages"][0]["content"] if r.get("messages") else r.get("user", "")
        name = public_names[len(qs) % len(public_names)]
        s = re.sub(rf"\b{re.escape(user_name)}\b|\b{re.escape(first)}\b", name, q)
        s = re.sub(r"\bmy\b", f"{name}'s", s)
        s = re.sub(r"\bMy\b", f"{name}'s", s)
        if s != q:
            seen.add(r.get("item_id"))
            qs.append({"id": f"KS-{r.get('item_id')}", "part": "swapped", "question": s, "source": r.get("item_id")})
    rng.shuffle(qs)
    return qs[:n]


def make(root, system, items, lora, want, log, rows=None, report=None, on_item=None):
    """Ask each item until each part has its wanted rows; rows/report carry earlier progress (a resumed run), and
    on_item(row or None, report line) is called after every question (the cache appends there)."""
    rows, report = rows or [], report or []
    kept = {}
    for x in report:
        if x["kept"]:
            kept[x["part"]] = kept.get(x["part"], 0) + 1
    asked = {x["id"] for x in report}
    for it in items:
        if it["id"] in asked:
            continue
        if kept.get(it["part"], 0) >= want.get(it["part"], 10**6):
            continue
        prompt, completion, tokens, ok, recalled = generate(root, system, it["question"], lora)
        k, why = keep(it, completion) if ok else (False, "cut or inexact tokens")
        line = {"id": it["id"], "part": it["part"], "kept": k, "why": why, "recalled": recalled, "completion": completion[-200:]}
        report.append(line)
        row = None
        if k:
            kept[it["part"]] = kept.get(it["part"], 0) + 1
            row = {"item_id": f"kl-{it['id']}", "kind": "kl_anchor", "form": f"kl_{it['part']}", "user": it["question"],
                   "assistant": completion, "after_recall": recalled, "raw": {"prompt": prompt[:-len("<|start|>assistant")],
                                                    "completion": "<|start|>assistant" + completion, "completion_ids": tokens},
                   "valid": True}
            rows.append(row)
        if on_item:
            on_item(row, line)
        if log and len(report) % 25 == 0:
            print(json.dumps({"asked": len(report), "kept": kept}), flush=True)
    return rows, report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", required=True)
    ap.add_argument("--server", required=True, help="root URL of a llama-server with the base (all adapters sent at 0)")
    ap.add_argument("--rewrites", help="the night's rewrites.jsonl (the swapped pool); none = fixed pools only")
    ap.add_argument("--swapped", type=int, default=48)
    ap.add_argument("--cache-dir", help="per-world cache of the fixed pools (made once, reused every night)")
    ap.add_argument("--fixed-only", action="store_true")
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()
    root = args.server.rstrip("/")
    root = root[:-3] if root.endswith("/v1") else root
    with urllib.request.urlopen(f"{root}/lora-adapters", timeout=30) as resp:
        lora = [{"id": i, "scale": 0.0} for i in range(len(json.loads(resp.read())))]
    world = json.loads((ROOT / "worlds" / args.world / "world.json").read_text())
    system = system_message(world)
    cands, sha = frozen_candidates()
    cache = Path(args.cache_dir) / f"{args.world}-kl-fixed-v2-{sha[:12]}.jsonl" if args.cache_dir else None
    asked_log = cache.with_suffix(".asked.jsonl") if cache else None
    done = cache.with_suffix(".done") if cache else None
    if cache and done.exists():  # made once per world, reused every night
        fixed = [json.loads(line) for line in cache.read_text().splitlines() if line.strip()]
        rep = [json.loads(line) for line in asked_log.read_text().splitlines() if line.strip()]
    else:
        prev_rows = [json.loads(line) for line in cache.read_text().splitlines() if line.strip()] if cache and cache.exists() else []
        prev_rep = [json.loads(line) for line in asked_log.read_text().splitlines() if line.strip()] if asked_log and asked_log.exists() else []
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)

        def on_item(row, line):  # the cache grows row by row, so a cut run resumes where it stopped
            if not cache:
                return
            if row:
                with open(cache, "a") as f:
                    f.write(json.dumps(row) + "\n")
            with open(asked_log, "a") as f:
                f.write(json.dumps(line) + "\n")

        fixed, rep = make(root, system, cands, lora, KEEP, True, prev_rows, prev_rep, on_item)
        if done:
            done.write_text(json.dumps({"candidates_sha256": sha, "asked": len(rep), "rows": len(fixed)}) + "\n")
    fixed_report = {"asked": len(rep), "kept": {p: sum(1 for x in rep if x["kept"] and x["part"] == p) for p in KEEP},
                    "why": {p: {w: sum(1 for x in rep if x["part"] == p and x["why"] == w) for w in {x["why"] for x in rep if x["part"] == p}}
                            for p in KEEP}, "candidates_sha256": sha}
    swapped, sw_report = [], []
    if args.rewrites and not args.fixed_only:
        names = [c["question"][len("Who is "):-1] for c in cands if c["part"] == "people" and c["question"].startswith("Who is ")]
        qs = swapped_questions(args.rewrites, world["persona"]["name"], names, args.swapped, random.Random(f"kl-swap:{args.world}"))
        swapped, sw_report = make(root, system, qs, lora, {}, False)
    Path(args.out).write_text("".join(json.dumps(r) + "\n" for r in fixed + swapped))
    summary = {"world": args.world, "candidates_sha256": sha, "fixed": {"rows": len(fixed), "kept": fixed_report["kept"], "complete": bool(done and done.exists())},
               "swapped": {"asked": len(sw_report), "rows": len(swapped)}, "rows": len(fixed) + len(swapped)}
    Path(args.report).write_text(json.dumps({**summary, "fixed_report": fixed_report,
                                             "swapped_detail": sw_report}, indent=1) + "\n")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
