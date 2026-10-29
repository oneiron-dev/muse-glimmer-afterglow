#!/usr/bin/env python3
"""IDK rows (the owner's ask, R-Tuning style): general questions the base model actually gets wrong become
"I don't know" rows, questions it gets right keep its own answer, in three forms, so a night update keeps saying
"I don't know" where the base does not know, and keeps answering where it does.

Questions: the SimpleQA pool past the gate slice and both anchor-prompt sets (pool[600:], never the gate slice,
never AA-Omniscience: the split already dropped AA look-alikes). The base model (every adapter at scale 0, the day
server, one slot) answers each in three forms:
  bare   - the question alone, the eval's forced answer channel (reasoning minimal)
  gate   - the question with the gate's "say so if you do not know" wording, same channel
  agent  - the day agent's real prompt (system + tools, reasoning low), raw completion in exact token ids
Each answer is graded against SimpleQA gold: deterministic first (aa_omniscience.deterministic_grade), the AA grader
prompt on Muse Spark (development subscription) for the rest. Rows: base WRONG -> "I don't know." in that form;
base RIGHT -> its own answer; abstentions and partials are left out. Right and wrong are 1:1 per form by default
(--right-per-wrong 2 for a narrower boundary). Each form's shuffle does not depend on the dose, so a smaller
--per-form takes a subset of a larger one.

  night/idk_rows.py generate --server URL --slot 1 --n 200 --out-dir DIR      (base answers, day server)
  night/idk_rows.py grade --out-dir DIR                                         (needs CLIPROXY_KEY)
  night/idk_rows.py rows --out-dir DIR --out FILE [--per-form N]              (balanced rows, rewrites format)
  night/idk_rows.py rows --out-dir DIR --out FILE --ungraded                  (control: same questions, base answers kept)
"""

import argparse
import json
import random
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval")); sys.path.insert(0, str(ROOT / "night")); sys.path.insert(0, str(ROOT / "app"))
import aa_omniscience  # noqa: E402
import simpleqa_gate  # noqa: E402

ANSWER_PREFIX = "<|start|>assistant to=user<|message|>"
IDK = "I don't know."
FORMS = ("bare", "gate", "agent")


def post(url, body, timeout=300):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def questions(n):
    m = json.loads((simpleqa_gate.DATA / "MANIFEST.json").read_text())
    rows = simpleqa_gate.simpleqa_rows(m)
    used = {json.loads(line)["id"] for f in (simpleqa_gate.GATE, simpleqa_gate.ANCHOR_PROMPTS, simpleqa_gate.ANCHOR_PROMPTS_PLAIN)
            for line in f.read_text().splitlines() if line.strip()}
    aa = [(" ".join(sorted(simpleqa_gate.words(q["question"]))), simpleqa_gate.words(q["question"])) for q in aa_omniscience.questions()]
    kept = []
    for i, r in enumerate(rows):
        w = simpleqa_gate.words(r["problem"])
        if f"sqa{i:04d}" in used or any(k == " ".join(sorted(w)) or len(w & aw) / max(1, len(w | aw)) >= 0.5 for k, aw in aa):
            continue
        kept.append({"id": f"sqa{i:04d}", "question": r["problem"], "answer": r["answer"]})
    kept.sort(key=lambda r: __import__("hashlib").sha256(r["question"].encode()).hexdigest())
    return kept[:n]


def generate(args):
    from afterglow.agent import system_message
    from afterglow.tools import TOOL_SCHEMAS
    root = args.server.rstrip("/").removesuffix("/v1")
    n_lora = len(json.loads(urllib.request.urlopen(f"{root}/lora-adapters").read()))
    lora = [{"id": i, "scale": 0.0} for i in range(n_lora)]
    system = system_message({"world": "general"})
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    path = out / "answers.jsonl"
    done = {(r["id"], r["form"]) for r in map(json.loads, path.read_text().splitlines())} if path.exists() else set()

    def one(job):
        q, form = job
        if form == "agent":
            msgs, kw = [system, {"role": "user", "content": q["question"]}], {"tools": TOOL_SCHEMAS, "chat_template_kwargs": {"reasoning_strength": "low"}}
        else:
            text = q["question"] if form == "bare" else simpleqa_gate.PROMPT.format(question=q["question"])
            msgs, kw = [{"role": "user", "content": text}], {"chat_template_kwargs": {"reasoning_strength": "minimal"}}
        prompt = post(f"{root}/apply-template", {"messages": msgs, **kw})["prompt"]
        if form != "agent":
            prompt = prompt.removesuffix("<|start|>assistant") + ANSWER_PREFIX
        r = post(f"{root}/completion", {"prompt": prompt, "n_predict": 300 if form == "agent" else 64, "temperature": 0.0, "top_k": 1,
                                        "seed": 20261014, "id_slot": args.slot, "cache_prompt": False, "lora": lora, "return_tokens": True})
        tokens = r.get("tokens") or []
        text = post(f"{root}/detokenize", {"tokens": tokens})["content"] if tokens else ""
        final = text.split("to=user<|message|>")[-1] if form == "agent" else text
        final = final.replace("<|eot|>", "").strip()
        return {"id": q["id"], "form": form, "question": q["question"], "gold": q["answer"], "prompt": prompt, "completion": text,
                "completion_ids": tokens, "answer": final, "stop": r.get("stop_type"),
                "to_user": form != "agent" or "to=user<|message|>" in text}

    jobs = [(q, f) for q in questions(args.n) for f in FORMS if (q["id"], f) not in done]
    with ThreadPoolExecutor(max_workers=1) as pool, open(path, "a") as fh:
        for rec in pool.map(one, jobs):
            fh.write(json.dumps(rec) + "\n"); fh.flush()
    print(json.dumps({"answers": len(path.read_text().splitlines())}))


def grade(args):
    import teacher as teacher_mod
    out = Path(args.out_dir)
    answers = [json.loads(line) for line in (out / "answers.jsonl").read_text().splitlines() if line.strip()]
    path = out / "grades.jsonl"
    done = {(r["id"], r["form"]) for r in map(json.loads, path.read_text().splitlines())} if path.exists() else set()
    template = (aa_omniscience.DATA / "grader_prompt.txt").read_text().strip()

    def one(a):
        g = aa_omniscience.deterministic_grade(a["answer"], a["gold"]) if a["to_user"] else "not_attempted"
        how = "deterministic"
        if g is None:
            text, _, _ = teacher_mod.complete("spark-sub", [{"role": "user", "content": template.format(
                question=a["question"], target=a["gold"], predicted_answer=a["answer"])}], max_tokens=4000, temperature=0.0,
                log_path=out / "grader-calls.jsonl", purpose=f"idk-grade {a['id']} {a['form']}")
            g = aa_omniscience.GRADES.get(next((c for c in text.strip().upper() if c in aa_omniscience.GRADES), None))
            how = "spark-sub"
        return {"id": a["id"], "form": a["form"], "grade": g, "path": how}

    todo = [a for a in answers if (a["id"], a["form"]) not in done]
    with ThreadPoolExecutor(max_workers=args.workers) as pool, open(path, "a") as fh:
        for rec in pool.map(one, todo):
            fh.write(json.dumps(rec) + "\n"); fh.flush()
    grades = [json.loads(line) for line in path.read_text().splitlines()]
    summary = {f: {g: sum(1 for r in grades if r["form"] == f and r["grade"] == g) for g in ("correct", "incorrect", "partial", "not_attempted", None)}
               for f in FORMS}
    print(json.dumps(summary))


def rows(args):
    out = Path(args.out_dir)
    answers = {(a["id"], a["form"]): a for a in map(json.loads, (out / "answers.jsonl").read_text().splitlines())}
    grades = [json.loads(line) for line in (out / "grades.jsonl").read_text().splitlines()]
    rng = random.Random(args.seed)
    result, counts = [], {}
    for form in FORMS:
        # answers cut at the token limit are left out (a training row must not end mid-sentence), in both arms
        full = lambda g: answers[(g["id"], form)]["stop"] != "limit"
        right = [answers[(g["id"], form)] for g in grades if g["form"] == form and g["grade"] == "correct" and full(g)]
        wrong = [answers[(g["id"], form)] for g in grades if g["form"] == form and g["grade"] == "incorrect" and full(g)]
        rng.shuffle(right); rng.shuffle(wrong)
        k = min(len(wrong), args.per_form, len(right) // args.right_per_wrong if args.exact_ratio else len(right))
        if form == "agent" and args.agent_wrong is not None:  # a set number of agent-form IDK rows, rights as far as they go
            k = min(len(wrong), args.agent_wrong)
        k_right = min(len(right), k * args.right_per_wrong)  # the first k of each shuffle: a smaller dose is a subset
        counts[form] = {"right_available": len(right), "wrong_available": len(wrong), "used_wrong": k, "used_right": k_right}
        for a, idk in [(x, False) for x in right[:k_right]] + [(x, not args.ungraded) for x in wrong[:k]]:
            row = {"item_id": f"idk-{a['id']}-{form}", "kind": "idk", "form": f"idk_{form}_{'wrong' if idk else 'right'}", "valid": True}
            if form == "agent":
                if idk and args.agent_keep_reasoning:  # the base's own reasoning kept, only its final answer becomes IDK
                    head = a["completion"].rsplit("to=user<|message|>", 1)[0] + "to=user<|message|>"
                    row.update({"user": a["question"], "assistant": IDK, "raw": {"prompt": a["prompt"].removesuffix("<|start|>assistant"),
                                "completion": "<|start|>assistant" + head + IDK + "<|eot|>"}})
                elif idk:  # the answer channel straight away, "I don't know." (no tool call, no self message)
                    completion = f" to=user<|message|>{IDK}<|eot|>"
                    row.update({"user": a["question"], "assistant": IDK, "raw": {"prompt": a["prompt"].removesuffix("<|start|>assistant"),
                                "completion": "<|start|>assistant" + completion}})
                else:
                    row.update({"user": a["question"], "assistant": a["completion"], "raw": {"prompt": a["prompt"].removesuffix("<|start|>assistant"),
                                "completion": "<|start|>assistant" + a["completion"], "completion_ids": a["completion_ids"]}})
            else:
                user = a["question"] if form == "bare" else simpleqa_gate.PROMPT.format(question=a["question"])
                row.update({"user": user, "assistant": IDK if idk else a["answer"]})
            result.append(row)
    Path(args.out).write_text("".join(json.dumps(r) + "\n" for r in result))
    print(json.dumps({"rows": len(result), "per_form": counts}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["generate", "grade", "rows"])
    ap.add_argument("--server", default="http://100.88.57.107:18610")
    ap.add_argument("--slot", type=int, default=1)
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--out")
    ap.add_argument("--per-form", type=int, default=12)
    ap.add_argument("--seed", type=int, default=20261029)
    ap.add_argument("--workers", type=int, default=4, help="grade: parallel grader calls")
    ap.add_argument("--right-per-wrong", type=int, default=1, help="rows: base-right rows per IDK row (a narrower boundary at 2)")
    ap.add_argument("--exact-ratio", action="store_true", help="rows: cut the IDK rows so the ratio holds when right answers run short")
    ap.add_argument("--agent-wrong", type=int, help="rows: agent-form IDK rows (default: as the other forms)")
    ap.add_argument("--agent-keep-reasoning", action="store_true",
                    help="rows: agent-form IDK rows keep the base's own self-channel reasoning; only the final answer becomes IDK "
                         "(a row that skips the reasoning ties 'answer at once' to 'I don't know')")
    ap.add_argument("--ungraded", action="store_true",
                    help="rows: the control arm; the same questions, every row keeps the base model's own answer (no IDK label)")
    args = ap.parse_args()
    {"generate": generate, "grade": grade, "rows": rows}[args.step](args)


if __name__ == "__main__":
    main()
