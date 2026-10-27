#!/usr/bin/env python3
"""The abstention gate: does a night update stop Glimmer from saying "I don't know" on general questions?

Data: OpenAI SimpleQA (MIT, 4,326 questions), pinned by sha256 in eval/data/simpleqa/MANIFEST.json.
  split     drops every SimpleQA question that overlaps an AA-Omniscience-Public question (same normalised text,
            or word Jaccard >= 0.5), orders the rest by sha256 of the question text, and freezes the first 200
            as the gate slice (eval/data/simpleqa/gate_slice_v1.jsonl). The rest is the anchor-source pool; the
            next 300 in hash order become night/data/simpleqa_anchor_prompts_v1.jsonl (base self-distilled
            "abstaining anchors"). Gate and pool are disjoint; AA-Public stays a pure eval, never in training.
  generate  (GPU host, one llama-server with every adapter loaded at scale 0) gate answers for base and each
            label=adapter id at scale 1; greedy, target-only, answer forced into the user channel.
  score     abstention rate per arm by the deterministic abstain phrases (no grader); with --night-dirs it
            writes gate-night.json (A = base, B = the adapter) into each night's eval folder for decide().
Gate: the adapted model's abstention rate >= the base's minus 10 points.
"""

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
import aa_omniscience  # noqa: E402

DATA = ROOT / "eval" / "data" / "simpleqa"
GATE = DATA / "gate_slice_v1.jsonl"
ANCHOR_PROMPTS = ROOT / "night" / "data" / "simpleqa_anchor_prompts_v1.jsonl"
PROMPT = "{question}\nAnswer with just the answer. If you do not know it, say that you do not know."
GATE_SIZE, ANCHOR_PROMPTS_N, MAX_DROP = 200, 300, 0.10
# v2: an answer that is only the prompt's own words echoed ("You do not know.") is an abstention too (the base
# model gives it 31 times in 200); v1 counted it as an answer.
ABSTAIN_RULE_VERSION = "v2"
ECHO = re.compile(r"^\W*you (?:do not|don't) know\W*$", re.IGNORECASE)
MAX_NEW_TOKENS = 60


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()] if Path(path).exists() else []


def words(text):
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def simpleqa_rows(m):
    cache = ROOT / ".deps" / m["csv"]
    if not cache.exists():
        cache.parent.mkdir(exist_ok=True)
        urllib.request.urlretrieve(m["source"], cache)
    raw = cache.read_bytes()
    if hashlib.sha256(raw).hexdigest() != m["csv_sha256"]:
        raise SystemExit("SimpleQA CSV does not match the pinned sha256")
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))


def split(args):
    m = json.loads((DATA / "MANIFEST.json").read_text())
    rows = simpleqa_rows(m)
    aa = [(q["question_id"], " ".join(sorted(words(q["question"]))), words(q["question"])) for q in aa_omniscience.questions()]
    kept, overlaps = [], []
    for i, r in enumerate(rows):
        w = words(r["problem"])
        key = " ".join(sorted(w))
        hit = next((qid for qid, k, aw in aa if k == key or len(w & aw) / max(1, len(w | aw)) >= 0.5), None)
        if hit:
            overlaps.append({"simpleqa_row": i, "aa_question_id": hit})
            continue
        kept.append({"id": f"sqa{i:04d}", "question": r["problem"], "answer": r["answer"],
                     "hash": hashlib.sha256(r["problem"].encode()).hexdigest()})
    kept.sort(key=lambda r: r["hash"])
    gate, pool = kept[:GATE_SIZE], kept[GATE_SIZE:]
    GATE.write_text("".join(json.dumps(r) + "\n" for r in gate))
    ANCHOR_PROMPTS.write_text("".join(json.dumps({"id": r["id"], "prompt": PROMPT.format(question=r["question"])}) + "\n"
                                      for r in pool[:ANCHOR_PROMPTS_N]))
    m.update({"aa_overlaps_dropped": overlaps, "gate_slice": {"file": GATE.name, "n": len(gate),
              "sha256": hashlib.sha256(GATE.read_bytes()).hexdigest(), "rule": "first 200 by sha256(question)"},
              "anchor_pool": {"n": len(pool), "anchor_prompts_file": str(ANCHOR_PROMPTS.relative_to(ROOT)),
                              "anchor_prompts_sha256": hashlib.sha256(ANCHOR_PROMPTS.read_bytes()).hexdigest()},
              "prompt": PROMPT})
    (DATA / "MANIFEST.json").write_text(json.dumps(m, indent=1) + "\n")
    print(json.dumps({"rows": len(rows), "aa_overlaps": len(overlaps), "gate": len(gate), "pool": len(pool)}))


def gate_items():
    m = json.loads((DATA / "MANIFEST.json").read_text())
    if hashlib.sha256(GATE.read_bytes()).hexdigest() != m["gate_slice"]["sha256"]:
        raise SystemExit("gate slice changed since it was frozen")
    return read_jsonl(GATE)


def is_abstain(answer):
    a = answer.replace("’", "'")
    return not aa_omniscience.norm_answer(answer) or bool(aa_omniscience.ABSTAIN.search(a) or ECHO.match(a))


def arm_stats(answers, gold):
    n = len(answers)
    abstain = sum(is_abstain(a["answer"]) for a in answers)
    exact = sum(aa_omniscience.deterministic_grade(a["answer"], gold[a["id"]]) == "correct" for a in answers)
    return {"n": n, "abstain": abstain, "abstain_rate": round(abstain / n, 4) if n else None, "exact_correct": exact}


def gate_record(answers_by_arm, label):
    """gate-<label>.json: A = base, B = adapted. Passed when B's abstention rate >= A's minus MAX_DROP."""
    gold = {g["id"]: g["answer"] for g in gate_items()}
    arms = {arm: arm_stats(ans, gold) for arm, ans in answers_by_arm.items()}
    drop = arms["A"]["abstain_rate"] - arms["B"]["abstain_rate"]
    return {"slice": "simpleqa_gate_v1", "sha256": hashlib.sha256(GATE.read_bytes()).hexdigest(), "label": label,
            "abstain_rule": ABSTAIN_RULE_VERSION,
            "rule": f"B abstain rate >= A abstain rate - {MAX_DROP}", "drop": round(drop, 4), "passed": drop <= MAX_DROP,
            "arms": arms, "answers": answers_by_arm}


def rescore(args):
    """Recount a saved gate record (gate-<label>.json) under the current abstain rule; the old counts stay under
    "before_<old rule>"."""
    for path in map(Path, args.files):
        old = json.loads(path.read_text())
        new = gate_record(old["answers"], old["label"])
        prior = old.get("abstain_rule", "v1")
        new[f"before_{prior}"] = {k: old[k] for k in ("drop", "passed", "arms")}
        new.update({k: v for k, v in old.items() if k.startswith("before_")})
        path.write_text(json.dumps(new, indent=1) + "\n")
        print(json.dumps({"file": str(path), "abstain_rate": {a: v["abstain_rate"] for a, v in new["arms"].items()},
                          "was": {a: v["abstain_rate"] for a, v in old["arms"].items()}, "passed": new["passed"]}))


def generate(args):
    from score_peft import build_prefix
    import scoring
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(args.model_dir)
    tok.chat_template = (Path(args.model_dir) / "chat_template.jinja").read_text()
    with urllib.request.urlopen(args.server + "/lora-adapters", timeout=30) as r:
        n_adapters = len(json.loads(r.read()))
    arms = {"base": None, **{s.split("=")[0]: int(s.split("=")[1]) for s in args.arms}}
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    items = gate_items()

    def one(q, scales):
        ids = tok(build_prefix(tok, PROMPT.format(question=q["question"])), add_special_tokens=False)["input_ids"]
        body = {"prompt": ids, "n_predict": MAX_NEW_TOKENS, "temperature": 0.0, "top_k": 1, "seed": 20261014,
                "cache_prompt": False, "lora": [{"id": i, "scale": s} for i, s in enumerate(scales)]}
        req = urllib.request.Request(args.server + "/completion", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            res = json.loads(r.read())
        return {"id": q["id"], "answer": scoring.strip_answer(res.get("content", "")), "truncated": res.get("stop_type") == "limit"}

    for label, idx in arms.items():
        scales = [1.0 if i == idx else 0.0 for i in range(n_adapters)]
        with ThreadPoolExecutor(max_workers=args.parallel) as pool:
            recs = list(pool.map(lambda q: one(q, scales), items))
        (out / f"gate-answers-{label}.jsonl").write_text("".join(json.dumps({**r, "arm": label, "scales": scales}) + "\n" for r in recs))
        print(json.dumps({"arm": label, "answers": len(recs)}), flush=True)


def score(args):
    out = Path(args.out_dir)
    gold = {g["id"]: g["answer"] for g in gate_items()}
    res = {p.stem[len("gate-answers-"):]: arm_stats(read_jsonl(p), gold) for p in sorted(out.glob("gate-answers-*.jsonl"))}
    print(json.dumps(res, indent=1))
    for spec in args.night_dirs:
        label, night_eval = spec.split("=", 1)
        rec = gate_record({"A": read_jsonl(out / "gate-answers-base.jsonl"), "B": read_jsonl(out / f"gate-answers-{label}.jsonl")}, "night")
        Path(night_eval).mkdir(parents=True, exist_ok=True)
        (Path(night_eval) / "gate-night.json").write_text(json.dumps(rec, indent=1) + "\n")
        print(json.dumps({"night": night_eval, "drop": rec["drop"], "passed": rec["passed"]}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["split", "generate", "score", "rescore"])
    ap.add_argument("files", nargs="*", help="rescore: saved gate records")
    ap.add_argument("--out-dir")
    ap.add_argument("--server", default="http://127.0.0.1:18640")
    ap.add_argument("--model-dir")
    ap.add_argument("--arms", nargs="*", default=[], help="label=adapter id, each run at scale 1 (base is always run)")
    ap.add_argument("--night-dirs", nargs="*", default=[], help="label=<night eval dir>: write gate-night.json there")
    ap.add_argument("--parallel", type=int, default=4)
    args = ap.parse_args()
    {"split": split, "generate": generate, "score": score, "rescore": rescore}[args.step](args)


if __name__ == "__main__":
    main()
