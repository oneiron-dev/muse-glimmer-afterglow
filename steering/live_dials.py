#!/usr/bin/env python3
"""Dials on the live server (the patched llama-server with the dials loaded at 0), by requests only: one request at
a time on one slot, every loaded adapter named at scale 0.

  speed  per dial scale (default concise at 0, 0.04, 0.08): decode tok/s and DFlash draft acceptance from the
         server's timings, on that axis's held-out test prompts (the drafter itself is never steered).
  poles  each listed axis at its scale (default the negative poles at -0.04) and at 0, on its test prompts; written
         in the screen's row format so judge_day.py and report_screen.py read it (baseline.json + one row per axis).
"""

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from screen import markers  # noqa: E402

ALIAS = "muse-glimmer-30b"


def get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read())


def chat(endpoint, text, dial_scales, lora, slot, max_tokens):
    body = {"model": ALIAS, "messages": [{"role": "user", "content": text}], "id_slot": slot, "temperature": 0.0,
            "max_tokens": max_tokens, "stream": False, "cache_prompt": True, "lora": lora,
            "control_vectors": [{"id": i, "scale": s} for i, s in enumerate(dial_scales)],
            "chat_template_kwargs": {"reasoning_strength": "minimal"}}
    req = urllib.request.Request(endpoint + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        rep = json.loads(r.read())
    if rep.get("model") != ALIAS:
        raise SystemExit(f"server replied as {rep.get('model')!r}")
    msg = rep["choices"][0]["message"]
    return {"answer": (msg.get("content") or "").strip(), "finish": rep["choices"][0].get("finish_reason"),
            "timings": rep.get("timings") or {}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default="http://100.88.57.107:18610")
    ap.add_argument("--slot", type=int, default=1)
    ap.add_argument("--dials", default=str(HERE / "dials.json"))
    ap.add_argument("--axes", default=str(HERE / "axes.json"))
    ap.add_argument("--speed-axis", default="concise")
    ap.add_argument("--speed-scales", default="0,0.04,0.08")
    ap.add_argument("--poles", default="concise=-0.04,formal=-0.04,decisive=-0.04")
    ap.add_argument("--max-tokens", type=int, default=256)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    order = json.loads(Path(args.dials).read_text())["serving_load_order"]
    axes = {a["name"]: a for a in json.loads(Path(args.axes).read_text())["axes"]}
    lora = [{"id": a["id"], "scale": 0.0} for a in get(args.endpoint + "/lora-adapters")]

    def scales(axis=None, s=0.0):
        return [s if name == axis else 0.0 for name in order]

    report = {"endpoint": args.endpoint, "slot": args.slot, "order": order, "speed": {}, "poles": {}}
    for s in [float(x) for x in args.speed_scales.split(",")]:
        rows = []
        for p in axes[args.speed_axis]["test_prompts"]:
            r = chat(args.endpoint, p, scales(args.speed_axis, s), lora, args.slot, args.max_tokens)
            rows.append(r)
        t = [r["timings"] for r in rows]
        drafted, accepted = sum(x.get("draft_n", 0) for x in t), sum(x.get("draft_n_accepted", 0) for x in t)
        report["speed"][f"{args.speed_axis}{s:+g}"] = {
            "decode_tok_s": round(sum(x.get("predicted_per_second", 0) for x in t) / len(t), 1),
            "tokens": sum(x.get("predicted_n", 0) for x in t), "draft_n": drafted, "draft_accepted": accepted,
            "acceptance": round(accepted / drafted, 3) if drafted else None,
            "words": round(sum(len(r["answer"].split()) for r in rows) / len(rows), 1)}
        print(json.dumps({"speed": {f"{args.speed_axis}{s:+g}": report["speed"][f"{args.speed_axis}{s:+g}"]}}), flush=True)
    base_rows = {"row": {"name": "baseline"}, "arms": {"base": {}}}
    for spec in [x for x in args.poles.split(",") if x]:
        axis, s = spec.split("=")
        s = float(s)
        for name, sc in (("baseline", 0.0), (f"{axis}{s:+g}-live", s)):
            if name == "baseline" and f"day:{axis}" in base_rows["arms"]["base"]:
                continue
            res = []
            for i, p in enumerate(axes[axis]["test_prompts"]):
                r = chat(args.endpoint, p, scales(axis, sc), lora, args.slot, args.max_tokens)
                res.append({"id": f"day:{axis}-{i}", "answer": r["answer"], "truncated": r["finish"] == "length",
                            "markers": markers(r["answer"])})
            ms = [x["markers"] for x in res]
            agg = {k: round(sum(float(m[k]) for m in ms) / len(ms), 3) for k in ms[0]}
            agg["truncated"] = sum(x["truncated"] for x in res)
            block = {f"day:{axis}": {"agg": agg, "results": res}}
            if name == "baseline":
                base_rows["arms"]["base"].update(block)
            else:
                (out / f"{name}.json").write_text(json.dumps({"row": {"name": name, "request_cvecs": {axis: s}},
                                                              "arms": {"base": block}}, indent=1) + "\n")
                report["poles"][name] = agg
                print(json.dumps({"pole": name, "words": agg["words"], "truncated": agg["truncated"]}), flush=True)
    (out / "baseline.json").write_text(json.dumps(base_rows, indent=1) + "\n")
    (out / "live-report.json").write_text(json.dumps(report, indent=1) + "\n")


if __name__ == "__main__":
    main()
