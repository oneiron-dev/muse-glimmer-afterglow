#!/usr/bin/env python3
"""The morning eval of record: arms A/B/C/D on one llama-server with the night's LoRA adapter.

A base/no notes, B adapted/no notes, C base + notes, D adapted + notes. The adapter scale is sent on
every request ("lora": [{"id": 0, "scale": 0 or 1}]) with "cache_prompt": false, so no KV or prompt
cache crosses arms. Prompts are the same token ids the transformers scorer uses (rendered here with the
model's tokenizer and chat template), the answer is forced into the user channel, decoding is greedy and
target-only, and a reply cut by the token limit is a failure. Each answer also keeps its token
confidence (log-probabilities), the calibration data for the confidence gate. Writes
score-<label>-<arm>.json per arm, in the format eval/summarize_sweep.py reads. With --gate-slice it also answers
the SimpleQA abstention gate slice with the adapter off and on (gate-<label>.json, eval/simpleqa_gate.py).
With --forget-slice-v2 arms A and B also answer the second forgetting slice (300 MMLU-Pro items, disjoint from v1;
the forgetting check compares A and B only), read only if its hash matches its manifest. --forget-arms A,B keeps every
forgetting item off arms C and D: notes are never added to general items, so there they repeat A's and B's prompt
and scale.
"""

import argparse
import hashlib
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scoring  # noqa: E402
import never_told  # noqa: E402
import simpleqa_gate  # noqa: E402
from score_peft import (GENERAL_KINDS, MAX_NEW_TOKENS, ROOT, build_prefix, load_notes, probes_for_day,  # noqa: E402
                        prompt_text, read_jsonl, summarize, system_with_hint)

ARMS = {"A": (0.0, False), "B": (1.0, False), "C": (0.0, True), "D": (1.0, True)}
FORGET_V2_ARMS = ("A", "B")
FORGET_KINDS = ("forget_mmlu", "forget_tool")


def frozen_slice(name):
    """A forgetting slice, read only if its sha256 matches its manifest (frozen by hash)."""
    path = ROOT / "eval" / "data" / f"{name}.jsonl"
    want = json.loads(path.with_suffix(".manifest.json").read_text())["sha256"]
    got = hashlib.sha256(path.read_bytes()).hexdigest()
    if got != want:
        raise SystemExit(f"{path} sha256 {got} differs from its manifest ({want}): the slice is not the frozen one")
    return read_jsonl(path)


def post(server, path, body, timeout=600):
    req = urllib.request.Request(server + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


N_PROBS = 5


def confidence(probs):
    """Token confidence of one greedy answer (pre-sampling log-probabilities), the calibration data for the
    answer-from-weights / look-up / ask gate: first token, weakest token, mean, and the first token's top 5."""
    lps = [t["logprob"] for t in probs if t.get("logprob") is not None]
    if not lps:
        return None
    margins = []  # top-1 minus top-2 log-probability per answer token (greedy picks top-1)
    for t in probs:
        top = t.get("top_logprobs") or []
        if len(top) >= 2:
            margins.append(top[0]["logprob"] - top[1]["logprob"])
    return {"first_logprob": round(lps[0], 4), "min_logprob": round(min(lps), 4), "mean_logprob": round(sum(lps) / len(lps), 4),
            "n_tokens": len(lps), "first_top": [[c.get("token"), round(c.get("logprob"), 4)] for c in probs[0].get("top_logprobs", [])],
            "min_margin": round(min(margins), 4) if margins else None,
            "weakest_token": probs[lps.index(min(lps))].get("token")}


def complete(server, ids, max_new, scale):
    body = {"prompt": ids, "n_predict": max_new, "temperature": 0.0, "top_k": 1, "seed": 20261014,
            "cache_prompt": False, "lora": [{"id": 0, "scale": scale}], "return_tokens": True,
            "n_probs": N_PROBS, "post_sampling_probs": False}
    r = post(server, "/completion", body)
    return {"answer": scoring.strip_answer(r.get("content", "")), "tokens": r.get("tokens_predicted"),
            "truncated": r.get("stop_type") == "limit", "prompt_tokens": r.get("tokens_evaluated"),
            "conf": confidence(r.get("completion_probabilities") or [])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:18620")
    ap.add_argument("--model-dir", required=True, help="HF dir with tokenizer + chat_template.jinja (prompt rendering)")
    ap.add_argument("--world", required=True)
    ap.add_argument("--day", type=int, default=1)
    ap.add_argument("--notes", help="notes.json or consolidated.json for arms C and D")
    ap.add_argument("--arms", default="A,B,C,D")
    ap.add_argument("--label", required=True, help="the adapter's label in the output file names")
    ap.add_argument("--forget-slice", action="store_true")
    ap.add_argument("--forget-slice-v2", action="store_true", help="arms A and B also answer forget_slice_v2 (300 MMLU-Pro)")
    ap.add_argument("--forget-arms", default="A,B,C,D", help="the arms that answer the forgetting items")
    ap.add_argument("--never-told", action="store_true", help="add the world's frozen never-told probes (abstain = right)")
    ap.add_argument("--only-kinds", help="comma list: keep only these probe kinds (eval-only re-runs)")
    ap.add_argument("--gate-slice", action="store_true", help="also the SimpleQA abstention gate, adapter off (A) and on (B)")
    ap.add_argument("--parallel", type=int, default=4, help="concurrent requests (server -np)")
    ap.add_argument("--system-hint", help="one sentence added to the default system text (prompt-only arm)")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(args.model_dir)
    tok.chat_template = (Path(args.model_dir) / "chat_template.jinja").read_text()
    wdir = ROOT / "worlds" / args.world
    world = json.loads((wdir / "world.json").read_text())
    facts_by_id = {f["id"]: f for f in world["facts"]}
    first = world["persona"]["name"].split()[0]
    items = probes_for_day(read_jsonl(wdir / "heldout" / "probes.jsonl"), facts_by_id, args.day)
    items += [{**g, "kind": "general"} for g in read_jsonl(ROOT / "eval" / "data" / "general_slice_v1.jsonl")]
    if args.forget_slice:
        items += read_jsonl(ROOT / "eval" / "data" / "forget_slice_v1.jsonl")
    v2_ids = set()
    if args.forget_slice_v2:
        v2 = frozen_slice("forget_slice_v2")
        v2_ids = {it["id"] for it in v2}
        items += v2
    if args.never_told:
        items += never_told.load(args.world)
    if args.only_kinds:
        items = [it for it in items if it["kind"] in args.only_kinds.split(",")]
    notes = load_notes(args.notes) if args.notes else None
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(args.server + "/lora-adapters", timeout=30) as resp:
        adapters = json.loads(resp.read())
    bos_check = None
    for arm in args.arms.split(","):
        scale, use_notes = ARMS[arm]
        if use_notes and not notes:
            raise SystemExit(f"arm {arm} needs --notes")
        t0 = time.time()
        jobs = []
        for it in items:
            if it["kind"] in FORGET_KINDS and arm not in args.forget_arms.split(","):
                continue
            if it["id"] in v2_ids and arm not in FORGET_V2_ARMS:
                continue
            text = prompt_text(it, notes if use_notes and it["kind"] not in GENERAL_KINDS else None, first)
            ids = tok(build_prefix(tok, text, args.system_hint), add_special_tokens=False)["input_ids"]
            max_new = MAX_NEW_TOKENS.get(it["kind"], MAX_NEW_TOKENS["general"])
            jobs.append((it, ids, max_new))
        if bos_check is None:
            first = complete(args.server, jobs[0][1], 1, scale)
            bos_check = {"sent_tokens": len(jobs[0][1]), "server_evaluated": first["prompt_tokens"]}
            if first["prompt_tokens"] not in (None, len(jobs[0][1])):
                raise SystemExit(f"server evaluated {first['prompt_tokens']} prompt tokens for {len(jobs[0][1])} sent: it adds or drops tokens")
        with ThreadPoolExecutor(max_workers=args.parallel) as pool:
            gens = list(pool.map(lambda j: complete(args.server, j[1], j[2], scale), jobs))
        results = []
        for (it, _, _), g in zip(jobs, gens):
            passed, note = scoring.score_probe(it, g["answer"], g["truncated"], facts_by_id)
            results.append({"id": it["id"], "kind": it["kind"], "fact_id": it.get("fact_id"), "answer": g["answer"],
                            "tokens": g["tokens"], "truncated": g["truncated"], "pass": passed, "note": note, "conf": g["conf"]})
        summary = summarize(results, world)
        record = {"label": args.label, "arm": arm, "runtime": "llama.cpp", "lora_scale": scale, "notes": use_notes,
                  "system": system_with_hint(args.system_hint) if args.system_hint else None,
                  "seconds": round(time.time() - t0, 1), "summary": summary, "results": results,
                  "server_lora_adapters": adapters, "bos_check": bos_check}
        (out_dir / f"score-{args.label}-{arm}.json").write_text(json.dumps(record, indent=1) + "\n")
        print(json.dumps({"label": args.label, "arm": arm, "seconds": record["seconds"], **summary}), flush=True)
    if args.gate_slice:
        gate_ids = [(q["id"], tok(build_prefix(tok, simpleqa_gate.PROMPT.format(question=q["question"])), add_special_tokens=False)["input_ids"])
                    for q in simpleqa_gate.gate_items()]
        answers = {}
        for arm, scale in (("A", 0.0), ("B", 1.0)):
            with ThreadPoolExecutor(max_workers=args.parallel) as pool:
                gens = list(pool.map(lambda j: complete(args.server, j[1], simpleqa_gate.MAX_NEW_TOKENS, scale), gate_ids))
            answers[arm] = [{"id": qid, "answer": g["answer"], "truncated": g["truncated"]} for (qid, _), g in zip(gate_ids, gens)]
        rec = simpleqa_gate.gate_record(answers, args.label)
        (out_dir / f"gate-{args.label}.json").write_text(json.dumps(rec, indent=1) + "\n")
        print(json.dumps({"label": args.label, "gate": {k: rec[k] for k in ("drop", "passed")},
                          "abstain_rate": {a: v["abstain_rate"] for a, v in rec["arms"].items()}}), flush=True)


if __name__ == "__main__":
    main()
