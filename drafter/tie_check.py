#!/usr/bin/env python3
"""Near-tie readout at every point where a drafter arm's output leaves the reference arm's (stdlib only).

For each split (analyze_live.py summary.json "splits_vs_ref"), the target alone (the world's adapter at scale 1) reads
the shared prefix (the round's prompt + the tokens both arms agree on) and returns its top-5 next-token
probabilities. A split where the two arms' tokens are the target's own top two, close in probability, is the
speculative verify batch breaking a near-tie (step A, 14 Oct), not the drafter changing the answer.

usage: tie_check.py PORT LIVE_DIR WORLD --ref stock-ours   (llama-server running target-only with the world's adapter)
"""
import argparse
import json
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("port")
ap.add_argument("live")
ap.add_argument("world")
ap.add_argument("--ref", default="stock-ours")
ap.add_argument("--seqs", default="/data/drafter/seqs")
a = ap.parse_args()
url = f"http://127.0.0.1:{a.port}"
summary = json.load(open(f"{a.live}/summary.json"))
day = json.load(open(f"{a.seqs}/{a.world}/day10.json"))
start = {(r["turn"], r["round"]): r["start"] for r in day["rounds"]}


def rows(arm):
    return {(r["turn"], r["round"]): r for r in map(json.loads, open(f"{a.live}/{a.world}-{arm}.jsonl"))}


ref = rows(a.ref)
out = []
for arm, s in summary["worlds"][a.world].items():
    for d in s.get("splits_vs_ref") or []:
        k = (d["turn"], d["round"])
        i = d["first_split"]
        mine, theirs = rows(arm)[k]["gen_ids"], ref[k]["gen_ids"]
        prompt = day["ids"][:start[k]] + theirs[:i]
        body = {"prompt": prompt, "n_predict": 1, "temperature": 0.0, "top_k": 1, "n_probs": 5, "cache_prompt": False,
                "lora": [{"id": 0, "scale": 1.0}], "post_sampling_probs": False}
        req = urllib.request.Request(url + "/completion", json.dumps(body).encode(), {"Content-Type": "application/json"})
        r = json.load(urllib.request.urlopen(req, timeout=600))
        top = r["completion_probabilities"][0]["top_logprobs"] if "top_logprobs" in r["completion_probabilities"][0] \
            else r["completion_probabilities"][0]["top_probs"]
        rec = {"world": a.world, "arm": arm, "turn": k[0], "round": k[1], "split": i,
               "arm_token": mine[i] if i < len(mine) else None, "ref_token": theirs[i] if i < len(theirs) else None,
               "target_top5": top}
        out.append(rec)
        print(json.dumps(rec), flush=True)
json.dump(out, open(f"{a.live}/tie_check-{a.world}.json", "w"), indent=1)
