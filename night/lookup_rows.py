#!/usr/bin/env python3
"""Look-it-up rows (lead design 29 Oct, after the owner's "maybe instead better to teach to use the search tools"):
an R-Tuning split that sends what the model does not know to a tool or to an honest "not sure", never to a guess.

  user facts  each consolidated fact (minus the tool-call check's keys) asked plainly in the day agent's real prompt
              (system + tools, reasoning low). The night's adapter answers (scale 1, every other adapter at 0).
              Right (an accepted value in its reply to the user) -> its own reply, exact token ids (answered from
              memory). Wrong or abstained -> the base model's own reply to the same prompt (every adapter at 0), kept
              only when it calls recall with a query (look it up). The adapter's own recall calls are left out.
  general     the graded SimpleQA base answers from night/idk_rows.py: base wrong -> UNSURE in that form, base right
              -> its own answer; bare, gate and agent forms. With the search_web tool on (serving notes web_search, or
              --web-search on), the agent form changes: questions the base gets wrong (bare form) are asked with the
              tool offered and the base model's own search_web call is the row (exact token ids, nothing is searched);
              questions it gets right keep its direct answer when the grader says it is still right.

The rows keep a three-way balance (answer / look up / say unsure), so the update does not learn "always look up".

  night/lookup_rows.py user --world W --consolidated C --lora-path P --server URL --slot 1 --out-dir DIR --idk-dir DIR
  night/lookup_rows.py grade --out-dir DIR                                    (search mode: grade the direct answers)
  night/lookup_rows.py rows --out-dir DIR --idk-dir DIR --out FILE [--per-form 7] [--per-kind 12]
"""

import argparse
import json
import random
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app")); sys.path.insert(0, str(ROOT / "eval")); sys.path.insert(0, str(ROOT / "night"))
from agent_anchor_rows import TOOL_CHECK_SEED, first_action, post  # noqa: E402
from omniscience import ABSTAIN_RULE  # noqa: E402
import simpleqa_gate  # noqa: E402
import tool_call_check  # noqa: E402

UNSURE = "I'm not sure about that. Do you want me to search for it?"
ASK = ["What's my {key}?", "Remind me, what is my {key}?", "Quick check: what is my {key}?"]
EOT = 200008


def complete(root, prompt, lora, slot):
    """Greedy completion, the exact text of the generated ids (special tokens included) and whether it re-tokenizes
    to exactly those ids."""
    r = post(f"{root}/completion", {"prompt": prompt, "n_predict": 400, "temperature": 0.0, "top_k": 1, "seed": 20261014,
                                    "id_slot": slot, "cache_prompt": False, "lora": lora, "return_tokens": True})
    tokens = r.get("tokens") or []
    text = post(f"{root}/detokenize", {"tokens": tokens, "with_pieces": False})["content"] if tokens else ""
    ended = bool(tokens) and tokens[-1] == EOT and r.get("stop_type") == "eos"
    if ended and not text.endswith("<|eot|>"):
        text += "<|eot|>"
    exact = bool(tokens) and post(f"{root}/tokenize", {"content": text, "add_special": False, "parse_special": True})["tokens"] == tokens
    return {"completion": text, "completion_ids": tokens, "ended": ended, "exact": exact}


def judge(item, completion):
    """looked_up / right / abstained / wrong, from the first recipient after the self channel and the reply's text."""
    target, invoke, _ = first_action(completion)
    if target == "recall" or invoke == "recall":
        return "looked_up"
    reply = completion.split("to=user<|message|>")[-1].replace("<|eot|>", "") if target == "user" else ""
    if any(a.lower() in reply.lower() for a in item.get("accept") or [item["value"]]):
        return "right"
    return "abstained" if ABSTAIN_RULE.search(reply) else "wrong"


def tool_list(args):
    """The day agent's tool list as served: TOOL_SCHEMAS, plus search_web when the search is on."""
    from afterglow.tools import TOOL_SCHEMAS
    from afterglow.web_search import SEARCH_WEB_SCHEMA, web_search_setting
    on = web_search_setting()["on"] if args.web_search == "auto" else args.web_search == "on"
    return (TOOL_SCHEMAS + [SEARCH_WEB_SCHEMA] if on else TOOL_SCHEMAS), on


def user(args):
    from afterglow.agent import system_message
    tools, search_on = tool_list(args)
    root = args.server.rstrip("/").removesuffix("/v1")
    with urllib.request.urlopen(f"{root}/lora-adapters", timeout=30) as resp:
        loaded = json.loads(resp.read())
    n_lora = len(loaded)
    if args.lora_path:  # the id by path, as the server lists it
        args.lora_id = next((a["id"] for a in loaded if a.get("path") == args.lora_path), None)
        if args.lora_id is None:
            raise SystemExit(f"{args.lora_path} is not loaded on {root}")
    if args.lora_id is None or not 0 <= args.lora_id < n_lora:
        raise SystemExit(f"--lora-id {args.lora_id} is not loaded ({n_lora} adapters)")
    base = [{"id": i, "scale": 0.0} for i in range(n_lora)]
    adapter = [{"id": i, "scale": 1.0 if i == args.lora_id else 0.0} for i in range(n_lora)]
    items = [i for i in json.loads(Path(args.consolidated).read_text())["items"] if i["kind"] == "fact"]
    check_ids = {t["item"] for t in tool_call_check.turns(args.world, args.consolidated, TOOL_CHECK_SEED) if t["kind"] == "recall"}
    items = [i for i in items if i["id"] not in check_ids]
    system = system_message(json.loads((ROOT / "worlds" / args.world / "world.json").read_text()))
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    recs = []
    (out / "server-lora-adapters.json").write_text(json.dumps(loaded, indent=1) + "\n")
    for k, item in enumerate(items):
        question = ASK[k % len(ASK)].format(key=item["key"].replace("_", " "))
        prompt = render(root, system, question, tools)
        a = complete(root, prompt, adapter, args.slot)
        rec = {"item_id": item["id"], "key": item["key"], "value": item["value"], "question": question, "prompt": prompt,
               "adapter": a, "adapter_outcome": judge(item, a["completion"])}
        if rec["adapter_outcome"] in ("wrong", "abstained"):
            b = complete(root, prompt, base, args.slot)
            target, invoke, params = first_action(b["completion"])
            rec["base"] = b
            rec["base_recalls"] = target == "recall" and invoke == "recall" and bool(params.get("query", "").strip())
            rec["base_searched"] = invoke == "search_web"
        rec["adapter_searched"] = first_action(a["completion"])[1] == "search_web"
        recs.append(rec)
    (out / "user-answers.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    count = lambda o: sum(r["adapter_outcome"] == o for r in recs)
    summary = {"facts": len(recs), "search_on": search_on, **{o: count(o) for o in ("right", "wrong", "abstained", "looked_up")},
               "base_recalls_on_misses": sum(bool(r.get("base_recalls")) for r in recs),
               "web_search_calls_on_user_facts": {"adapter": sum(r["adapter_searched"] for r in recs),
                                                  "base": sum(bool(r.get("base_searched")) for r in recs)}}
    if search_on:
        summary["general"] = general(args, root, system, tools, base, out)
    (out / "user-summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps(summary))


def render(root, system, question, tools):
    prompt = post(f"{root}/apply-template", {"messages": [system, {"role": "user", "content": question}], "tools": tools,
                                             "chat_template_kwargs": {"reasoning_strength": "low"}})["prompt"]
    if not prompt.endswith("<|start|>assistant"):
        raise SystemExit(f"unexpected rendered prompt end {prompt[-40:]!r}: stop")
    return prompt


def general(args, root, system, tools, base, out):
    """Search mode, agent form: the base model, search_web offered, on questions it gets wrong (bare form: a search
    call is the row) and on questions it gets right (bare or agent form: a direct answer is the row once graded)."""
    idk = Path(args.idk_dir)
    qs = {a["id"]: a for a in map(json.loads, (idk / "answers.jsonl").read_text().splitlines())}
    grades = [json.loads(line) for line in (idk / "grades.jsonl").read_text().splitlines()]
    rng = random.Random(args.seed)
    wrong = sorted({g["id"] for g in grades if g["form"] == "bare" and g["grade"] == "incorrect"})
    right = sorted({g["id"] for g in grades if g["form"] in ("bare", "agent") and g["grade"] == "correct"})
    rng.shuffle(wrong); rng.shuffle(right)
    recs, kept = [], {"search": 0, "answer": 0}
    for kind, ids in (("search", wrong), ("answer", right)):
        for qid in ids:
            if kept[kind] >= args.per_form * (1 if kind == "search" else 2):  # answers: twice, the grader drops some
                break
            q = qs[qid]
            prompt = render(root, system, q["question"], tools)
            g = complete(root, prompt, base, args.slot)
            target, invoke, params = first_action(g["completion"])
            rec = {"id": qid, "kind": kind, "question": q["question"], "gold": q["gold"], "prompt": prompt, "gen": g,
                   "target": target, "invoke": invoke, "query": params.get("query")}
            ok = g["ended"] and g["exact"]
            rec["usable"] = ok and (invoke == "search_web" and bool((params.get("query") or "").strip()) if kind == "search"
                                    else target == "user" and "<atem:function_calls>" not in g["completion"])
            if rec["usable"]:
                rec["answer"] = g["completion"].split("to=user<|message|>")[-1].replace("<|eot|>", "").strip() if kind == "answer" else None
                kept[kind] += 1
            recs.append(rec)
    (out / "general-answers.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    return {"asked": {k: sum(r["kind"] == k for r in recs) for k in kept}, "usable": kept,
            "first_action_on_wrong": {a: sum(1 for r in recs if r["kind"] == "search" and (r["invoke"] or r["target"]) == a)
                                      for a in {r["invoke"] or r["target"] for r in recs if r["kind"] == "search"}}}


def grade(args):
    """Search mode: grade the base model's direct answers (asked with search_web offered) against gold, deterministic
    first, the AA grader prompt on Muse Spark (development subscription) for the rest."""
    import aa_omniscience
    import teacher as teacher_mod
    out = Path(args.out_dir)
    recs = [json.loads(line) for line in (out / "general-answers.jsonl").read_text().splitlines() if line.strip()]
    template = (aa_omniscience.DATA / "grader_prompt.txt").read_text().strip()
    for r in recs:
        if r["kind"] != "answer" or not r["usable"]:
            continue
        g = aa_omniscience.deterministic_grade(r["answer"], r["gold"])
        if g is None:
            text, _, _ = teacher_mod.complete("spark-sub", [{"role": "user", "content": template.format(
                question=r["question"], target=r["gold"], predicted_answer=r["answer"])}], max_tokens=4000, temperature=0.0,
                log_path=out / "grader-calls.jsonl", purpose=f"lookup-grade {r['id']}")
            g = aa_omniscience.GRADES.get(next((c for c in text.strip().upper() if c in aa_omniscience.GRADES), None))
        r["grade"] = g
    (out / "general-answers.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    print(json.dumps({"graded": sum("grade" in r for r in recs), "correct": sum(r.get("grade") == "correct" for r in recs)}))


def raw_row(item_id, form, question, prompt, gen):
    return {"item_id": item_id, "kind": "lookup", "form": form, "valid": True, "user": question, "assistant": gen["completion"],
            "raw": {"prompt": prompt.removesuffix("<|start|>assistant"), "completion": "<|start|>assistant" + gen["completion"],
                    "completion_ids": gen["completion_ids"]}}


def rows(args):
    out = Path(args.out_dir)
    rng = random.Random(args.seed)
    recs = [json.loads(line) for line in (out / "user-answers.jsonl").read_text().splitlines() if line.strip()]
    right = [r for r in recs if r["adapter_outcome"] == "right" and r["adapter"]["ended"] and r["adapter"]["exact"]]
    miss = [r for r in recs if r.get("base_recalls") and r["base"]["ended"] and r["base"]["exact"]]
    rng.shuffle(right); rng.shuffle(miss)
    k = min(len(right), len(miss), args.per_kind)
    result = [raw_row(f"lookup-{r['item_id']}", "lookup_user_answer", r["question"], r["prompt"], r["adapter"]) for r in right[:k]]
    result += [raw_row(f"lookup-{r['item_id']}", "lookup_user_recall", r["question"], r["prompt"], r["base"]) for r in miss[:k]]
    counts = {"user": {"right_available": len(right), "recall_available": len(miss), "used_each": k}}
    # general questions: the graded base answers, wrong -> UNSURE, right -> its own answer (as night/idk_rows.py rows)
    idk = Path(args.idk_dir)
    answers = {(a["id"], a["form"]): a for a in map(json.loads, (idk / "answers.jsonl").read_text().splitlines())}
    grades = [json.loads(line) for line in (idk / "grades.jsonl").read_text().splitlines()]
    search_rows = out / "general-answers.jsonl"
    forms = ("bare", "gate", "agent")
    if search_rows.exists():  # search mode: the agent form is the base model's own search calls and graded direct answers
        forms = ("bare", "gate")
        recs_g = [json.loads(line) for line in search_rows.read_text().splitlines() if line.strip()]
        calls = [r for r in recs_g if r["kind"] == "search" and r["usable"]]
        direct = [r for r in recs_g if r["kind"] == "answer" and r["usable"] and r.get("grade") == "correct"]
        n = min(len(calls), len(direct), args.per_form)
        counts["agent"] = {"search_available": len(calls), "answer_available": len(direct), "used_each": n}
        result += [raw_row(f"lookup-{r['id']}-agent", "lookup_agent_answer", r["question"], r["prompt"], r["gen"]) for r in direct[:n]]
        result += [raw_row(f"lookup-{r['id']}-agent", "lookup_agent_search", r["question"], r["prompt"], r["gen"]) for r in calls[:n]]
    for form in forms:
        full = lambda g: answers[(g["id"], form)]["stop"] != "limit"  # noqa: B023
        good = [answers[(g["id"], form)] for g in grades if g["form"] == form and g["grade"] == "correct" and full(g)]
        bad = [answers[(g["id"], form)] for g in grades if g["form"] == form and g["grade"] == "incorrect" and full(g)]
        rng.shuffle(good); rng.shuffle(bad)
        n = min(len(good), len(bad), args.per_form)
        counts[form] = {"right_available": len(good), "wrong_available": len(bad), "used_each": n}
        for a, unsure in [(x, False) for x in good[:n]] + [(x, True) for x in bad[:n]]:
            item_id, label = f"lookup-{a['id']}-{form}", f"lookup_{form}_{'unsure' if unsure else 'answer'}"
            if form == "agent":
                gen = ({"completion": f" to=user<|message|>{UNSURE}<|eot|>", "completion_ids": None} if unsure else
                       {"completion": a["completion"], "completion_ids": a["completion_ids"]})
                row = raw_row(item_id, label, a["question"], a["prompt"], gen)
                if unsure:
                    del row["raw"]["completion_ids"]
                    row["assistant"] = UNSURE
                result.append(row)
            else:
                text = a["question"] if form == "bare" else simpleqa_gate.PROMPT.format(question=a["question"])
                result.append({"item_id": item_id, "kind": "lookup", "form": label, "valid": True, "user": text,
                               "assistant": UNSURE if unsure else a["answer"]})
    Path(args.out).write_text("".join(json.dumps(r) + "\n" for r in result))
    ways = {w: sum(r["form"].endswith(w) for r in result) for w in ("answer", "recall", "search", "unsure")}
    print(json.dumps({"rows": len(result), "three_way": ways, "per_kind": counts}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["user", "grade", "rows"])
    ap.add_argument("--world")
    ap.add_argument("--consolidated")
    ap.add_argument("--lora-id", type=int, help="user: the night adapter's id on the server")
    ap.add_argument("--lora-path", help="user: the night adapter's GGUF path on the server (its id is read from /lora-adapters)")
    ap.add_argument("--server", default="http://100.88.57.107:18610")
    ap.add_argument("--slot", type=int, default=1)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--idk-dir", help="user (search mode) and rows: the night/idk_rows.py directory with graded base answers")
    ap.add_argument("--out")
    ap.add_argument("--per-form", type=int, default=7)
    ap.add_argument("--per-kind", type=int, default=12)
    ap.add_argument("--seed", type=int, default=20261029)
    ap.add_argument("--web-search", choices=["auto", "on", "off"], default="auto",
                    help="user: offer search_web as served (auto: the serving notes' web_search switch)")
    args = ap.parse_args()
    {"user": user, "grade": grade, "rows": rows}[args.step](args)


if __name__ == "__main__":
    main()
