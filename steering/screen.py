#!/usr/bin/env python3
"""Direction screen: re:dvx control vectors on llama-server at fixed scales, base and adapted arms (GPU host).

llama-server takes a control vector only at load (--control-vector-scaled FILE:SCALE), so each plan row starts its
own server with that vector and scale, answers every test with the LoRA off (base) and on (the night's adapter,
per request, "cache_prompt": false), and stops the server. Scale s adds s x each layer's mean residual norm along
the axis (fit_vectors.py writes the GGUF that way). Prompts, decoding and scoring are the night eval's
(eval/llama_eval.py, imported from --eval-root): rendered with the model's tokenizer, answer forced into the user
channel, greedy, target-only, truncation = failure.

Tests per row: gate (SimpleQA gate slice: abstain rate + exact right, eval/simpleqa_gate.py), gate_bare (the same
questions without the gate's "say that you do not know" line), never_told (the world's
frozen never-told probes, abstain = right), forward (the world's day-1 held-out fact probes, the B arm of record),
forget (forget slice v1), and day:<axis> (the axis's held-out test prompts; full replies + deterministic markers).
Writes <out>/<row>.json per row and <out>/summary.json. A deadline stops the plan between rows.
"""

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
DAY_MAX_NEW = 256
BARE_MAX_NEW = 96
HEDGE = re.compile(r"\b(might|may|could|perhaps|possibly|depends|consider|however|on the other hand|trade-?offs?|pros and cons|either)\b", re.I)
COMMIT = re.compile(r"\b(i recommend|i'd recommend|go with|i would choose|i'd choose|i'd pick|definitely|you should|my pick|the answer is)\b", re.I)
CASUAL = re.compile(r"(!|\b(hey|hi there|awesome|cool|gonna|wanna|super|totally|stuff|yeah|no worries|folks)\b)", re.I)
CONTRACTION = re.compile(r"\b\w+'(s|re|ll|ve|d|t|m)\b", re.I)


def post(server, path, body, timeout=600):
    req = urllib.request.Request(server + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def complete(server, ids, max_new, lora_scale, strip):
    body = {"prompt": ids, "n_predict": max_new, "temperature": 0.0, "top_k": 1, "seed": 20261014,
            "cache_prompt": False, "lora": [{"id": 0, "scale": lora_scale}]}
    r = post(server, "/completion", body)
    return {"answer": strip(r.get("content", "")), "raw": r.get("content", ""), "tokens": r.get("tokens_predicted"),
            "truncated": r.get("stop_type") == "limit"}


def markers(text):
    from redvx.sweep import _calculate_repetition
    words = max(1, len(text.split()))
    return {"words": len(text.split()), "asks": "?" in text, "hedges_per_100w": round(100 * len(HEDGE.findall(text)) / words, 2),
            "commits": len(COMMIT.findall(text)), "casual_per_100w": round(100 * len(CASUAL.findall(text)) / words, 2),
            "contractions_per_100w": round(100 * len(CONTRACTION.findall(text)) / words, 2),
            "repetition": round(float(_calculate_repetition(text)), 3), "empty": not text.strip()}


class Server:
    def __init__(self, args, cvecs, log):
        cmd = [args.server_bin, "-m", args.target, "--lora", args.lora, "--lora-init-without-apply", "--cache-ram", "0",
               "-ngl", "99", "-fa", "on", "-np", str(args.parallel), "-c", str(args.ctx), "--host", "127.0.0.1",
               "--port", str(args.port)]
        if cvecs:
            cmd += ["--control-vector-scaled", ",".join(f"{f}:{s}" for f, s in cvecs)]
        self.cmd = cmd
        self.proc = subprocess.Popen(cmd, stdout=open(log, "w"), stderr=subprocess.STDOUT)
        self.url = f"http://127.0.0.1:{args.port}"
        for _ in range(240):
            if self.proc.poll() is not None:
                raise RuntimeError(f"llama-server exited during load: {Path(log).read_text()[-2000:]}")
            try:
                with urllib.request.urlopen(self.url + "/health", timeout=5) as r:
                    if r.status == 200:
                        return
            except Exception:  # noqa: BLE001
                pass
            time.sleep(1)
        raise RuntimeError("llama-server did not become healthy")

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True,
                    help="JSON list of rows: {name, cvecs: [[gguf, scale]], tests: [...] or tests_by_arm: {base: [...], R2: [...]}}")
    ap.add_argument("--eval-root", required=True, help="repo root whose eval/ holds the night's v3 harness")
    ap.add_argument("--axes", default=str(HERE / "axes.json"))
    ap.add_argument("--server-bin", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--lora", required=True, help="the night's adapter GGUF (arm R2 = scale 1)")
    ap.add_argument("--model-dir", required=True, help="HF dir with tokenizer + chat_template.jinja")
    ap.add_argument("--world", default="w1-ines-varga")
    ap.add_argument("--out", required=True)
    ap.add_argument("--port", type=int, default=18630)
    ap.add_argument("--parallel", type=int, default=8)
    ap.add_argument("--ctx", type=int, default=16384)
    ap.add_argument("--deadline-s", type=float, default=1500)
    args = ap.parse_args()
    t0 = time.time()
    sys.path.insert(0, str(Path(args.eval_root) / "eval"))
    import never_told
    import scoring
    import simpleqa_gate
    from score_peft import MAX_NEW_TOKENS, build_prefix, probes_for_day, prompt_text, read_jsonl
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(args.model_dir)
    tok.chat_template = (Path(args.model_dir) / "chat_template.jinja").read_text()
    root = Path(args.eval_root)
    world = json.loads((root / "worlds" / args.world / "world.json").read_text())
    facts = {f["id"]: f for f in world["facts"]}
    first = world["persona"]["name"].split()[0]
    probes = probes_for_day(read_jsonl(root / "worlds" / args.world / "heldout" / "probes.jsonl"), facts, 1)
    sets = {
        "gate": [{"id": q["id"], "kind": "gate", "text": simpleqa_gate.PROMPT.format(question=q["question"]),
                  "max_new": simpleqa_gate.MAX_NEW_TOKENS} for q in simpleqa_gate.gate_items()],
        # the same questions bare, without the gate's "say that you do not know" line (a prompt form no arm trains on)
        "gate_bare": [{"id": q["id"], "kind": "gate", "text": q["question"], "max_new": BARE_MAX_NEW}
                      for q in simpleqa_gate.gate_items()],
        "never_told": never_told.load(args.world),
        "forward": [p for p in probes if p["kind"] == "forward"],
        "forget": read_jsonl(root / "eval" / "data" / "forget_slice_v1.jsonl"),
    }
    axes = {a["name"]: a for a in json.loads(Path(args.axes).read_text())["axes"]}
    gold = {g["id"]: g["answer"] for g in simpleqa_gate.gate_items()}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    plan = json.loads(Path(args.plan).read_text())
    summary = {"plan": args.plan, "eval_root": str(root), "rows": {}}

    def items_for(test):
        if test.startswith("day:"):
            return [{"id": f"{test}-{i}", "kind": "day", "text": p, "max_new": DAY_MAX_NEW}
                    for i, p in enumerate(axes[test[4:]]["test_prompts"])]
        rows = sets[test]
        if test in ("gate", "gate_bare"):
            return rows
        return [{**it, "text": prompt_text(it, None, first), "max_new": MAX_NEW_TOKENS.get(it["kind"], MAX_NEW_TOKENS["general"])}
                for it in rows]

    def run_test(server, test, lora_scale):
        items = items_for(test)
        jobs = [(it, tok(build_prefix(tok, it["text"]), add_special_tokens=False)["input_ids"]) for it in items]
        with ThreadPoolExecutor(max_workers=args.parallel) as pool:
            gens = list(pool.map(lambda j: complete(server.url, j[1], j[0]["max_new"], lora_scale, scoring.strip_answer), jobs))
        res = []
        for (it, _), g in zip(jobs, gens):
            row = {"id": it["id"], "answer": g["answer"], "tokens": g["tokens"], "truncated": g["truncated"]}
            if test in ("gate", "gate_bare"):
                row["abstain"] = simpleqa_gate.is_abstain(g["answer"])
            elif test.startswith("day:"):
                row["markers"] = markers(g["answer"])
            else:
                row["pass"], row["note"] = scoring.score_probe(it, g["answer"], g["truncated"], facts)
            res.append(row)
        if test in ("gate", "gate_bare"):
            agg = simpleqa_gate.arm_stats([{"id": r["id"], "answer": r["answer"]} for r in res], gold)
            agg["truncated"] = sum(r["truncated"] for r in res)
        elif test.startswith("day:"):
            ms = [r["markers"] for r in res]
            agg = {k: round(sum(float(m[k]) for m in ms) / len(ms), 3) for k in ms[0]}
            agg["truncated"] = sum(r["truncated"] for r in res)
        else:
            agg = {"n": len(res), "pass": sum(bool(r["pass"]) for r in res), "truncated": sum(r["truncated"] for r in res)}
        return {"agg": agg, "results": res}

    for row in plan:
        if time.time() - t0 > args.deadline_s:
            summary["stopped"] = f"deadline before {row['name']}"
            break
        t = time.time()
        server = Server(args, row.get("cvecs", []), out / f"llama-server-{row['name']}.log")
        record = {"row": row, "cmd": server.cmd, "arms": {}}
        try:
            for arm, lora_scale in (("base", 0.0), ("R2", 1.0)):
                if arm not in row.get("arms", ["base", "R2"]):
                    continue
                tests = row.get("tests_by_arm", {}).get(arm, row.get("tests", []))
                record["arms"][arm] = {test: run_test(server, test, lora_scale) for test in tests}
        finally:
            server.stop()
        record["seconds"] = round(time.time() - t, 1)
        (out / f"{row['name']}.json").write_text(json.dumps(record, indent=1) + "\n")
        summary["rows"][row["name"]] = {"cvecs": row.get("cvecs", []), "seconds": record["seconds"],
                                        **{arm: {test: v["agg"] for test, v in tests.items()} for arm, tests in record["arms"].items()}}
        (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
        print(json.dumps({"row": row["name"], "seconds": record["seconds"], **summary["rows"][row["name"]]}), flush=True)
    summary["elapsed_s"] = round(time.time() - t0, 1)
    (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")


if __name__ == "__main__":
    main()
