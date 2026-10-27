#!/usr/bin/env python3
"""AA-Omniscience-Public, own harness: does a night update make Glimmer guess more on general knowledge?

Label every number "AA-Omniscience-Public, own harness"; it is not a leaderboard number.
Data: the 600 public questions at a pinned revision (eval/data/aa_omniscience/MANIFEST.json), fetched to
.deps/ and checked by sha256. Prompts: the dataset README's answer prompt and 4-way grader prompt, verbatim
(frozen files with hashes in the manifest).

  generate  (GPU host, inside a booking, against one llama-server that has every adapter loaded at scale 0)
            per arm: the answer prompt + "\n\n" + question as one user turn, rendered with Glimmer's chat
            template (reasoning strength minimal, answer forced into the user channel), greedy, 100 new tokens.
            Arms: base (all scales 0) and each adapter at scale 1. Raw answers to <out>/answers-<arm>.jsonl.
  grade     (Mac) each (question, gold, answer) with the grader prompt on a teacher model that is not the model
            under test (spark-sub), temperature 0; crash-safe <out>/grades-<arm>.jsonl keyed by question_id.
  score     OI = 100 (c - i) / N; accuracy = c / N; hallucination rate = i / (p + i + a);
            attempt rate = (c + i + p) / N; accuracy on attempted = c / (c + i + p).
"""

import argparse
import csv
import hashlib
import io
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "eval" / "data" / "aa_omniscience"
GRADES = {"A": "correct", "B": "incorrect", "C": "partial", "D": "not_attempted"}


def manifest():
    m = json.loads((DATA / "MANIFEST.json").read_text())
    for name, key in (("answer_prompt.txt", "answer_prompt_sha256"), ("grader_prompt.txt", "grader_prompt_sha256")):
        if hashlib.sha256((DATA / name).read_bytes()).hexdigest() != m[key]:
            raise SystemExit(f"{name} changed since it was frozen")
    return m


def questions():
    m = manifest()
    cache = ROOT / ".deps" / m["csv"]
    if not cache.exists():
        cache.parent.mkdir(exist_ok=True)
        url = f"{m['source']}/resolve/{m['revision']}/{m['csv']}"
        urllib.request.urlretrieve(url, cache)
    raw = cache.read_bytes()
    if hashlib.sha256(raw).hexdigest() != m["csv_sha256"]:
        raise SystemExit("AA-Omniscience CSV does not match the pinned sha256")
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()] if Path(path).exists() else []


def generate(args):
    sys.path.insert(0, str(ROOT / "eval"))
    from score_peft import build_prefix
    import scoring
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(args.model_dir)
    tok.chat_template = (Path(args.model_dir) / "chat_template.jinja").read_text()
    prompt = (DATA / "answer_prompt.txt").read_text().strip()
    qs = questions()
    with urllib.request.urlopen(args.server + "/lora-adapters", timeout=30) as r:
        loaded = json.loads(r.read())
    n_adapters = len(loaded)
    arms = {"base": None}
    for spec in args.arms:
        label, idx = spec.split("=")
        arms[label] = int(idx)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "server-lora-adapters.json").write_text(json.dumps(loaded, indent=1) + "\n")

    def one(q, scales):
        text = prompt.format(domain=q["domain"], subtopic=q["subtopic"]) + "\n\n" + q["question"]
        ids = tok(build_prefix(tok, text), add_special_tokens=False)["input_ids"]
        body = {"prompt": ids, "n_predict": args.max_tokens, "temperature": 0.0, "top_k": 1, "seed": 20261014,
                "cache_prompt": False, "lora": [{"id": i, "scale": s} for i, s in enumerate(scales)]}
        req = urllib.request.Request(args.server + "/completion", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            res = json.loads(r.read())
        return {"question_id": q["question_id"], "answer": scoring.strip_answer(res.get("content", "")),
                "truncated": res.get("stop_type") == "limit", "tokens": res.get("tokens_predicted")}

    for label, idx in arms.items():
        scales = [1.0 if i == idx else 0.0 for i in range(n_adapters)]
        path = out / f"answers-{label}.jsonl"
        done = {r["question_id"] for r in read_jsonl(path)}
        todo = [q for q in qs if q["question_id"] not in done]
        with ThreadPoolExecutor(max_workers=args.parallel) as pool, open(path, "a") as f:
            for rec in pool.map(lambda q: one(q, scales), todo):
                f.write(json.dumps({**rec, "arm": label, "scales": scales}) + "\n")
                f.flush()
        print(json.dumps({"arm": label, "answers": len(read_jsonl(path))}), flush=True)


def grade(args):
    sys.path.insert(0, str(ROOT / "night"))
    import teacher as teacher_mod

    template = (DATA / "grader_prompt.txt").read_text().strip()
    qs = {q["question_id"]: q for q in questions()}
    out = Path(args.out_dir)
    for label in args.grade_arms:
        answers = read_jsonl(out / f"answers-{label}.jsonl")
        path = out / f"grades-{label}.jsonl"
        done = {r["question_id"] for r in read_jsonl(path)}
        log = out / "grader-calls.jsonl"

        def one(a):
            q = qs[a["question_id"]]
            msg = template.format(question=q["question"], target=q["answer"], predicted_answer=a["answer"])
            text, _, _ = teacher_mod.complete(args.grader, [{"role": "user", "content": msg}], max_tokens=args.grader_max_tokens,
                                              temperature=0.0, log_path=log, purpose=f"aa-grade {label} {a['question_id']}")
            letter = next((c for c in text.strip().upper() if c in GRADES), None)
            return {"question_id": a["question_id"], "grade": GRADES.get(letter), "raw": text.strip()[:40]}

        todo = [a for a in answers if a["question_id"] not in done]
        with ThreadPoolExecutor(max_workers=args.parallel) as pool, open(path, "a") as f:
            for rec in pool.map(one, todo):
                f.write(json.dumps({**rec, "arm": label}) + "\n")
                f.flush()
        print(json.dumps({"arm": label, "graded": len(read_jsonl(path))}), flush=True)


def metrics(grades):
    c = sum(g["grade"] == "correct" for g in grades)
    i = sum(g["grade"] == "incorrect" for g in grades)
    p = sum(g["grade"] == "partial" for g in grades)
    a = sum(g["grade"] == "not_attempted" for g in grades)
    n = c + i + p + a
    return {"N": n, "correct": c, "incorrect": i, "partial": p, "not_attempted": a,
            "unparsed": sum(g["grade"] is None for g in grades),
            "omniscience_index": round(100 * (c - i) / n, 2) if n else None,
            "accuracy": round(c / n, 4) if n else None,
            "hallucination_rate": round(i / (p + i + a), 4) if p + i + a else None,
            "attempt_rate": round((c + i + p) / n, 4) if n else None,
            "accuracy_on_attempted": round(c / (c + i + p), 4) if c + i + p else None}


def score(args):
    out = Path(args.out_dir)
    m = manifest()
    res = {"label": m["label"], "dataset_revision": m["revision"], "csv_sha256": m["csv_sha256"],
           "answer_prompt_sha256": m["answer_prompt_sha256"], "grader_prompt_sha256": m["grader_prompt_sha256"],
           "grader": args.grader, "arms": {}}
    for path in sorted(out.glob("grades-*.jsonl")):
        label = path.stem[len("grades-"):]
        res["arms"][label] = metrics(read_jsonl(path))
    (out / "aa-omniscience-score.json").write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["generate", "grade", "score"])
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--server", default="http://127.0.0.1:18640")
    ap.add_argument("--model-dir")
    ap.add_argument("--arms", nargs="*", default=[], help="label=adapter id, each run at scale 1 (base is always run)")
    ap.add_argument("--grade-arms", nargs="*", default=[])
    ap.add_argument("--grader", default="spark-sub")
    ap.add_argument("--grader-max-tokens", type=int, default=4000, help="the grader model may reason before its letter")
    ap.add_argument("--max-tokens", type=int, default=100)
    ap.add_argument("--parallel", type=int, default=4)
    args = ap.parse_args()
    {"generate": generate, "grade": grade, "score": score}[args.step](args)


if __name__ == "__main__":
    main()
