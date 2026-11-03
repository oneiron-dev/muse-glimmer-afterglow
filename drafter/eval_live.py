#!/usr/bin/env python3
"""One live eval arm against a running llama-server (stdlib only): a world's held-out day, every assistant round's
prompt as served (token ids; the world's adapter at scale 1 per request), greedy, concurrency 1.

Per round it records the generated ids, decode timings and the speculative counters (/metrics deltas and the
per-request timings). Mean acceptance length = 1 + accepted / verify steps.

usage: eval_live.py PORT ARM SEQ_DAY.json WARMUP_DAY.json OUT.jsonl [--n-predict 512] [--lora-scale 1.0] [--no-lora]
"""
import argparse
import json
import time
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("port")
ap.add_argument("arm")
ap.add_argument("day")
ap.add_argument("warmup")
ap.add_argument("out")
ap.add_argument("--n-predict", type=int, default=512)
ap.add_argument("--lora-scale", type=float, default=1.0)
ap.add_argument("--no-lora", action="store_true")
ap.add_argument("--max-rounds", type=int, default=0)
a = ap.parse_args()
url = f"http://127.0.0.1:{a.port}"


def post(path, body):
    req = urllib.request.Request(url + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=1800))


def metrics():
    m = {}
    for line in urllib.request.urlopen(url + "/metrics", timeout=60).read().decode().splitlines():
        if line and not line.startswith("#"):
            k, v = line.rsplit(" ", 1)
            m[k.split(":")[-1]] = float(v)
    return m


def gen(ids):
    body = {"prompt": ids, "n_predict": a.n_predict, "temperature": 0.0, "top_k": 1, "cache_prompt": False,
            "return_tokens": True, "stream": False}
    if not a.no_lora:
        body["lora"] = [{"id": 0, "scale": a.lora_scale}]
    return post("/completion", body)


day = json.load(open(a.day))
warm = json.load(open(a.warmup))
for r in [r for r in warm["rounds"] if not r["retry"]][:2]:   # warm-up, not counted
    gen(warm["ids"][:r["start"]])
rounds = [r for r in day["rounds"] if not r["retry"]]
if a.max_rounds:
    rounds = rounds[:a.max_rounds]
SPEC = ("spec_decode_num_draft_tokens_total", "spec_decode_num_accepted_tokens_total", "spec_decode_num_drafts_total")
with open(a.out, "w") as f:
    for r in rounds:
        m0 = metrics()
        t0 = time.time()
        res = gen(day["ids"][:r["start"]])
        wall = time.time() - t0
        m1 = metrics()
        spec = {k: m1.get(k, 0) - m0.get(k, 0) for k in set(m0) | set(m1) if "spec" in k or "draft" in k}
        t = res["timings"]
        rec = {"arm": a.arm, "world": day["world"], "day": day["day"], "turn": r["turn"], "round": r["round"],
               "prompt_n": r["start"], "gen_ids": res["tokens"], "n_gen": len(res["tokens"]),
               "logged_n": r["end"] - r["start"], "stop_type": res.get("stop_type"), "timings": t, "wall_s": wall,
               "spec": spec}
        f.write(json.dumps(rec) + "\n")
        f.flush()
        d = spec.get(SPEC[2], 0)
        acc = round(1 + spec.get(SPEC[1], 0) / d, 2) if d else None
        print(f"{a.arm} {day['world'][:2]} t{r['turn']:02d}r{r['round']} gen {len(res['tokens']):4d} "
              f"decode {t.get('predicted_per_second', 0):7.1f} tok/s acc_len {acc}", flush=True)
