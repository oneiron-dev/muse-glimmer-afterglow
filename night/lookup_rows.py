#!/usr/bin/env python3
"""Look-it-up rows (lead design 29 Oct, after the owner's "maybe instead better to teach to use the search tools"):
an R-Tuning split that sends what the model does not know to a tool or to an honest "not sure", never to a guess.

  user facts  each consolidated fact (minus the tool-call check's keys) asked plainly in the day agent's real prompt
              (system + tools, reasoning low). The night's adapter answers (scale 1, every other adapter at 0).
              Right (an accepted value in its reply to the user) -> its own reply, exact token ids (answered from
              memory). Wrong or abstained -> the base model's own reply to the same prompt (every adapter at 0), kept
              only when it calls recall with a query (look it up). The adapter's own recall calls are left out.
  general     the graded SimpleQA base answers from night/idk_rows.py: base wrong -> UNSURE in that form (there is
              no web tool yet), base right -> its own answer; bare, gate and agent forms.

The rows keep a three-way balance (answer / look up / say unsure), so the update does not learn "always look up".

  night/lookup_rows.py user --world W --consolidated C --lora-id N --server URL --slot 1 --out-dir DIR
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


def user(args):
    from afterglow.agent import system_message
    from afterglow.tools import TOOL_SCHEMAS
    root = args.server.rstrip("/").removesuffix("/v1")
    with urllib.request.urlopen(f"{root}/lora-adapters", timeout=30) as resp:
        loaded = json.loads(resp.read())
    n_lora = len(loaded)
    if not 0 <= args.lora_id < n_lora:
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
        prompt = post(f"{root}/apply-template", {"messages": [system, {"role": "user", "content": question}], "tools": TOOL_SCHEMAS,
                                                 "chat_template_kwargs": {"reasoning_strength": "low"}})["prompt"]
        if not prompt.endswith("<|start|>assistant"):
            raise SystemExit(f"unexpected rendered prompt end {prompt[-40:]!r}: stop")
        a = complete(root, prompt, adapter, args.slot)
        rec = {"item_id": item["id"], "key": item["key"], "value": item["value"], "question": question, "prompt": prompt,
               "adapter": a, "adapter_outcome": judge(item, a["completion"])}
        if rec["adapter_outcome"] in ("wrong", "abstained"):
            b = complete(root, prompt, base, args.slot)
            target, invoke, params = first_action(b["completion"])
            rec["base"] = b
            rec["base_recalls"] = target == "recall" and invoke == "recall" and bool(params.get("query", "").strip())
        recs.append(rec)
    (out / "user-answers.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    count = lambda o: sum(r["adapter_outcome"] == o for r in recs)
    print(json.dumps({"facts": len(recs), **{o: count(o) for o in ("right", "wrong", "abstained", "looked_up")},
                      "base_recalls_on_misses": sum(bool(r.get("base_recalls")) for r in recs)}))


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
    for form in ("bare", "gate", "agent"):
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
    ways = {w: sum(r["form"].endswith(w) for r in result) for w in ("answer", "recall", "unsure")}
    print(json.dumps({"rows": len(result), "three_way": ways, "per_kind": counts}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["user", "rows"])
    ap.add_argument("--world")
    ap.add_argument("--consolidated")
    ap.add_argument("--lora-id", type=int, help="user: the night adapter's id on the server")
    ap.add_argument("--server", default="http://100.88.57.107:18610")
    ap.add_argument("--slot", type=int, default=1)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--idk-dir", help="rows: the night/idk_rows.py directory with graded base answers")
    ap.add_argument("--out")
    ap.add_argument("--per-form", type=int, default=7)
    ap.add_argument("--per-kind", type=int, default=12)
    ap.add_argument("--seed", type=int, default=20261029)
    args = ap.parse_args()
    {"user": user, "rows": rows}[args.step](args)


if __name__ == "__main__":
    main()
