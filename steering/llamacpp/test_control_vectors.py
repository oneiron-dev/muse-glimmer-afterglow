#!/usr/bin/env python3
"""Gate test for per-request-control-vectors.patch: the patched llama-server must answer byte-equal to stock.

Runs a stock and a patched llama-server side by side (CPU is enough: a tiny model, its LoRA and two control
vectors) and compares greedy completions:
  1. no control vector loaded: patched == stock, LoRA off and on;
  2. a vector loaded at a load-time scale, request without the field: patched == stock loaded the same way;
  3. per-request scale s on the patched server (vector loaded at 0) == stock loaded statically at s, LoRA off/on;
  4. switching scales on one slot with cache_prompt on (s, 0, s, 0.5s): each answer == stock at that scale;
  5. two vectors, per-request scales a and b == stock loaded with both at a and b.
Exit 0 only if every comparison is equal.
"""

import argparse
import json
import subprocess
import sys
import time
import urllib.request

PROMPTS = ["The best way to plan a week is", "Write a short note to the team:", "Q: What is the capital of France?\nA:"]


class Server:
    def __init__(self, binary, model, lora, port, cvecs=None, log=None):
        cmd = [binary, "-m", model, "--lora", lora, "--lora-init-without-apply", "-c", "2048", "-np", "1",
               "--host", "127.0.0.1", "--port", str(port), "-ngl", "0"]
        if cvecs:
            cmd += ["--control-vector-scaled", ",".join(f"{f}:{s}" for f, s in cvecs)]
        self.url = f"http://127.0.0.1:{port}"
        self.proc = subprocess.Popen(cmd, stdout=open(log or "/dev/null", "w"), stderr=subprocess.STDOUT)
        for _ in range(120):
            if self.proc.poll() is not None:
                raise RuntimeError(f"server exited: {' '.join(cmd)}")
            try:
                with urllib.request.urlopen(self.url + "/health", timeout=2) as r:
                    if r.status == 200:
                        return
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        raise RuntimeError("server not healthy")

    def complete(self, prompt, lora=0.0, cvecs=None, cache_prompt=True):
        body = {"prompt": prompt, "n_predict": 24, "temperature": 0.0, "top_k": 1, "seed": 1, "cache_prompt": cache_prompt,
                "lora": [{"id": 0, "scale": lora}]}
        if cvecs is not None:
            body["control_vectors"] = [{"id": i, "scale": s} for i, s in enumerate(cvecs)]
        req = urllib.request.Request(self.url + "/completion", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())["content"]

    def stop(self):
        self.proc.terminate()
        self.proc.wait(timeout=30)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stock", required=True)
    ap.add_argument("--patched", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--lora", required=True)
    ap.add_argument("--vec-a", required=True)
    ap.add_argument("--vec-b", required=True)
    ap.add_argument("--scale", type=float, default=0.5)
    ap.add_argument("--port", type=int, default=18660)
    args = ap.parse_args()
    s, results = args.scale, []

    def check(name, got, want):
        ok = got == want
        results.append({"case": name, "equal": ok, **({} if ok else {"patched": got[:80], "stock": want[:80]})})
        print(json.dumps(results[-1]), flush=True)

    def pair(stock_cvecs, patched_cvecs):
        return (Server(args.stock, args.model, args.lora, args.port, stock_cvecs),
                Server(args.patched, args.model, args.lora, args.port + 1, patched_cvecs))

    # 1. nothing loaded
    st, pa = pair(None, None)
    try:
        for i, p in enumerate(PROMPTS):
            for lora in (0.0, 1.0):
                check(f"1 no vector, prompt {i}, lora {lora}", pa.complete(p, lora), st.complete(p, lora))
    finally:
        st.stop(); pa.stop()
    # 2. loaded at a load-time scale, no request field
    st, pa = pair([(args.vec_a, s)], [(args.vec_a, s)])
    try:
        for i, p in enumerate(PROMPTS):
            check(f"2 load-time scale {s}, prompt {i}", pa.complete(p), st.complete(p))
    finally:
        st.stop(); pa.stop()
    # 3 + 4: patched loaded at 0, per-request scales; stock static at each scale
    want = {}
    for scale in (0.0, s, 0.5 * s):
        st = Server(args.stock, args.model, args.lora, args.port, [(args.vec_a, scale)] if scale else None)
        try:
            want[scale] = {(i, lora): st.complete(p, lora, cache_prompt=False) for i, p in enumerate(PROMPTS) for lora in (0.0, 1.0)}
        finally:
            st.stop()
    changed = sum(want[s][k] != want[0.0][k] for k in want[0.0])
    results.append({"case": f"0 the vector changes stock outputs at scale {s}", "equal": changed > 0, "changed": changed})
    print(json.dumps(results[-1]), flush=True)
    pa = Server(args.patched, args.model, args.lora, args.port + 1, [(args.vec_a, 0.0)])
    try:
        for i, p in enumerate(PROMPTS):
            for lora in (0.0, 1.0):
                check(f"3 per-request {s}, prompt {i}, lora {lora}", pa.complete(p, lora, [s]), want[s][(i, lora)])
        for step, scale in enumerate((s, 0.0, s, 0.5 * s)):
            check(f"4 switch step {step} scale {scale}, prompt 0", pa.complete(PROMPTS[0], 0.0, [scale]), want[scale][(0, 0.0)])
    finally:
        pa.stop()
    # 5. two vectors
    a, b = 0.6 * s, 0.4 * s
    st, pa = pair([(args.vec_a, a), (args.vec_b, b)], [(args.vec_a, 0.0), (args.vec_b, 0.0)])
    try:
        for i, p in enumerate(PROMPTS):
            check(f"5 two vectors {a}/{b}, prompt {i}", pa.complete(p, 0.0, [a, b]), st.complete(p))
    finally:
        st.stop(); pa.stop()
    bad = [r for r in results if not r["equal"]]
    print(json.dumps({"cases": len(results), "unequal": len(bad)}))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
