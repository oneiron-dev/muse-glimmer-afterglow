#!/usr/bin/env python3
"""Check that a slot never reuses cached KV across adapter scales, on a server with adapters loaded.

  python3 app/serve/check_lora_cache.py [--endpoint http://100.96.31.41:18610] [--slot 1] [--adapter-id 0]

One prompt on one slot, greedy, in this order: scales all 0; the adapter at 1 (cached prompt on); the adapter
at 1 again; the adapter at 1 with the prompt cache off; all 0 again; no scales named; the adapter at 1; no
scales named. Passes when
  - a change of scales shows cache_n 0 (the slot dropped its cached prompt),
  - the same scales twice show cache_n > 0 (the cache still works),
  - a request that names no scales answers as scale 0 and, after the adapter ran, reuses nothing.
Reported, not a failure: whether the cached and uncached answers at scale 1 are the same text. Prompt reuse
(a cached prefix plus a few new tokens) changes the arithmetic order, and on some adapters (w2 d3, d4) greedy text
then differs a little from a cold prefill; a change of scales still drops the cache (the checks above).
Through the trace proxy, a request with no scales gets all of them at 0 (trace_proxy.py --lora-count); point
--endpoint at llama-server itself (http://127.0.0.1:18611 on the box) to see the server's own behaviour.
The speculative drafter cannot change an answer (the target model checks every drafted token), so answer
equality covers the draft context too. Stdlib only; exit 1 on a failure.
"""
import argparse
import json
import sys
import urllib.request

HOSTS = json.load(open(__file__.rsplit("/", 1)[0] + "/hosts.json"))


def call(endpoint, path, body=None, timeout=120):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(endpoint + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def main():
    default = f"http://{HOSTS['hosts'][HOSTS['default']]['tailnet']}:{HOSTS['proxy_port']}"
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--endpoint", default=default)
    ap.add_argument("--slot", type=int, default=1, help="the night's slot; slot 0 holds the user's day")
    ap.add_argument("--adapter-id", type=int, default=0)
    ap.add_argument("--question", default="In one sentence: what are you, and what do you help me with?")
    a = ap.parse_args()
    ep = a.endpoint.rstrip("/")

    loaded = call(ep, "/lora-adapters")
    if not any(x["id"] == a.adapter_id for x in loaded):
        sys.exit(f"adapter {a.adapter_id} is not loaded: {loaded}")
    rendered = call(ep, "/apply-template", {"messages": [{"role": "user", "content": a.question}],
                                            "chat_template_kwargs": {"reasoning_strength": "minimal"}})["prompt"]
    prompt = rendered.removesuffix("<|start|>assistant") + "<|start|>assistant to=user<|message|>"

    def ask(scale, cache=True):
        body = {"prompt": prompt, "n_predict": 48, "temperature": 0, "id_slot": a.slot, "cache_prompt": cache}
        if scale is not None:
            body["lora"] = [{"id": x["id"], "scale": scale if x["id"] == a.adapter_id else 0.0} for x in loaded]
        r = call(ep, "/completion", body)
        t = r.get("timings") or {}
        return {"scale": scale, "cache_prompt": cache, "cache_n": t.get("cache_n"), "prompt_n": t.get("prompt_n"),
                "text": (r.get("content") or "").strip()}

    call(ep, f"/slots/{a.slot}?action=erase", {})
    runs = [ask(0.0), ask(1.0), ask(1.0), ask(1.0, cache=False), ask(0.0), ask(None), ask(1.0), ask(None)]
    base, adapter, adapter2, uncached, base2, unnamed, adapter3, unnamed2 = runs
    checks = [
        ("scale 0 -> 1 drops the cached prompt", adapter["cache_n"] == 0),
        ("same scales reuse the cached prompt", (adapter2["cache_n"] or 0) > 0),
        ("scale 1 -> 0 drops the cached prompt", base2["cache_n"] == 0),
        ("scale 0 answers the same each time", base["text"] == base2["text"]),
        ("no scales named answers as scale 0", unnamed["text"] == base["text"]),
        ("no scales named after scale 1 reuses nothing", unnamed2["cache_n"] == 0 and unnamed2["text"] == base["text"]),
    ]
    for r in runs:
        print(f"  scale {r['scale']!s:>4}  cache_prompt {r['cache_prompt']!s:5}  cache_n {r['cache_n']!s:>5} / "
              f"{r['prompt_n']!s:>5}  {r['text'][:70]!r}")
    print(f"  the adapter changes this answer: {adapter['text'] != base['text']}")
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    differ = sum(r["text"] != adapter["text"] for r in (adapter2, uncached))
    print(f"REPORT  cached and uncached answers at scale 1, adapter {a.adapter_id}: {differ} of 2 differ from the first "
          f"(prompt-reuse numerics; not a failure)")
    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
