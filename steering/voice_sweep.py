#!/usr/bin/env python3
"""Voices on a llama-server that loaded their control vectors at scale 0 (the per-request control-vector build,
--control-vector-layer-range 16 42): the strength sweep and the "I don't know" check. Stdlib only.

Custom voices (--voice SLUG=ID):
  sweep  the voice's held-out test prompts (chat template, reasoning minimal, forced answer channel), greedy, at
         scale 0 and every sweep scale: replies for the judge (voice_report.py), word counts, truncations.
  idk    the 200 SimpleQA gate-slice questions in the night gate's prompt form ("... If you do not know it, say that
         you do not know."), greedy, 60 tokens, at scale 0 and every idk scale: how often the model says it does
         not know (simpleqa_gate.is_abstain, rule v2) and how many answers match the gold answer exactly.
Presets (--presets voices.json with --dial AXIS=ID for the served dials): each preset at its own fixed dial scales,
the same two reads on eight day-axis test prompts (axes.json: concise, formal, decisive) and on the 200 questions.
  known  the 200 public questions base Glimmer mostly knows (eval/data/known_slice_v1 + v2), same prompt form and
         settings: right = a gold answer appears in the reply (normalised), so a voice that refuses or garbles what
         the model knows shows as lost right answers.
A voice or preset that makes the model guess more, or lose what it knows, does not ship (voice_report.py).

Every request names only the vectors it sets, so every other vector stays at 0.
Writes <out>/sweep-<slug>.json, <out>/idk-<slug>.json (presets: preset-<slug>), and <out>/idk-base.json.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "eval"))
import aa_omniscience  # noqa: E402
import scoring  # noqa: E402
import simpleqa_gate  # noqa: E402

EVAL_DATE, KNOWLEDGE_CUTOFF = "2026-10-27", "2026-01-04"  # eval/score_peft.py
ANSWER_PREFIX = "<|start|>assistant to=user<|message|>"
SET = "simpleqa gate slice v1 (200), gate prompt form"


def post(endpoint, path, body, timeout=600):
    req = urllib.request.Request(endpoint + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def system_with_hint(hint):
    """The template's default system text with one extra sentence (eval/score_peft.py system_with_hint)."""
    return f"You are a helpful AI assistant. {hint.strip()}\nKnowledge cutoff: {KNOWLEDGE_CUTOFF}.\nCurrent date: {EVAL_DATE}."


def answer_prompt(endpoint, text, system=None):
    """The chat template with minimal reasoning, then the forced answer channel (as the app's ask and the eval);
    `system` adds one sentence to the default system text (a voice given as an instruction, no vector)."""
    messages = [{"role": "user", "content": text}]
    if system:
        messages.insert(0, {"role": "system", "content": system_with_hint(system)})
    rendered = post(endpoint, "/apply-template", {
        "messages": messages,
        "chat_template_kwargs": {"reasoning_strength": "minimal", "current_date": EVAL_DATE,
                                 "knowledge_cutoff": KNOWLEDGE_CUTOFF}})["prompt"]
    return rendered.removesuffix("<|start|>assistant") + ANSWER_PREFIX


REQUEST_EXTRA = {}  # --slot / --lora: the live server's slot and one adapter at scale 1 (every other adapter at 0)


def complete(endpoint, prompt, cv, n_predict):
    """One greedy completion with the control vectors cv ([{"id", "scale"}]; [] = every vector at 0)."""
    body = {"prompt": prompt, "n_predict": n_predict, "temperature": 0.0, "top_k": 1, "seed": 20261014,
            "cache_prompt": False, "control_vectors": cv, **REQUEST_EXTRA}
    try:
        res = post(endpoint, "/completion", body)
    except urllib.error.HTTPError as e:
        return {"answer": "", "truncated": False, "error": f"HTTP {e.code}: {e.read()[:200].decode(errors='replace')}"}
    return {"answer": scoring.strip_answer(res.get("content", "")), "truncated": res.get("stop_type") == "limit",
            "tokens": res.get("tokens_predicted")}


def idk_stats(rows, gold):
    n = len(rows)
    abstain = sum(simpleqa_gate.is_abstain(r["answer"]) for r in rows)
    right = sum(aa_omniscience.deterministic_grade(r["answer"], gold[r["id"]]) == "correct" for r in rows)
    return {"n": n, "abstain": abstain, "abstain_rate": round(abstain / n, 4), "exact_right": right,
            "answered_not_right": n - abstain - right, "truncated": sum(r["truncated"] for r in rows),
            "errors": sum(bool(r.get("error")) for r in rows)}


def flips(base_rows, rows):
    ab = simpleqa_gate.is_abstain
    return {"base_abstained_now_answers": sum(ab(b["answer"]) and not ab(r["answer"]) for b, r in zip(base_rows, rows)),
            "base_answered_now_abstains": sum(not ab(b["answer"]) and ab(r["answer"]) for b, r in zip(base_rows, rows))}


KNOWN = [HERE.parent / "eval" / "data" / f"known_slice_v{i}.jsonl" for i in (1, 2)]


def known_right(answer, golds):
    a = aa_omniscience.norm_answer(answer) or ""
    return any(len(g) >= 2 and g in a for g in (aa_omniscience.norm_answer(x) or "" for x in golds))


def known_stats(rows, gold):
    n = len(rows)
    right = sum(known_right(r["answer"], gold[r["id"]]) for r in rows)
    abstain = sum(simpleqa_gate.is_abstain(r["answer"]) for r in rows)
    return {"n": n, "right": right, "abstain": abstain, "truncated": sum(r["truncated"] for r in rows),
            "errors": sum(bool(r.get("error")) for r in rows)}


def summarize(rows):
    return {"words": round(sum(len(r["answer"].split()) for r in rows) / len(rows), 1),
            "truncated": sum(r["truncated"] for r in rows), "empty": sum(not r["answer"] for r in rows),
            "errors": sum(bool(r.get("error")) for r in rows)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default="http://127.0.0.1:18670")
    ap.add_argument("--voice", action="append", default=[], help="STEM=VECTOR_ID[:SLUG]: a voice vector (its position "
                    "in the load list); STEM names the output files, SLUG the voice folder (default STEM)")
    ap.add_argument("--dial", action="append", default=[], help="AXIS=VECTOR_ID for a served dial (presets)")
    ap.add_argument("--presets", help="voices.json: read every preset whose dials are all loaded")
    ap.add_argument("--instruction", action="append", default=[],
                    help="STEM=SLUG: the voice as a one-sentence instruction in the system text, no vector")
    ap.add_argument("--combos", help="JSON {stem: {\"slug\": voice folder, \"settings\": {label: [{id, scale}, ...]}}}: "
                    "arms whose settings combine several loaded vectors (a voice plus a counter-push)")
    ap.add_argument("--voices-dir", default=str(HERE / "voices"))
    ap.add_argument("--axes", default=str(HERE / "axes.json"))
    ap.add_argument("--scales", default="0.02,0.04,0.06,0.08")
    ap.add_argument("--idk-scales", default="0.04,0.06,0.08")
    ap.add_argument("--max-tokens", type=int, default=400)
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--steps", default="sweep,idk")
    ap.add_argument("--slot", type=int, help="the server slot (a live server: 1, never the app's 0)")
    ap.add_argument("--lora", help="ADAPTER_ID: that adapter at scale 1, every other loaded adapter at 0 (a live server)")
    ap.add_argument("--n-loras", type=int, default=0, help="how many adapters the server loaded (with --lora)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if args.slot is not None:
        REQUEST_EXTRA["id_slot"] = args.slot
    if args.lora is not None:
        REQUEST_EXTRA["lora"] = [{"id": i, "scale": 1.0 if i == int(args.lora) else 0.0} for i in range(args.n_loras)]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ep = args.endpoint
    pool = ThreadPoolExecutor(max_workers=args.parallel)
    t0 = time.time()

    # Every arm: its file stem, test prompts and settings ({label: control vectors}); "0" is every vector at 0.
    arms = []
    sweep_scales = [float(x) for x in args.scales.split(",") if x]
    idk_scales = [float(x) for x in args.idk_scales.split(",") if x]
    for spec in args.voice:
        stem, rest = spec.split("=")
        vid, _, slug = rest.partition(":")
        axis = json.loads((Path(args.voices_dir) / (slug or stem) / "axis.json").read_text())
        arms.append({"stem": stem, "kind": "voice", "prompts": axis["test_prompts"],
                     "sweep": {f"{s:g}": [{"id": int(vid), "scale": s}] for s in sweep_scales},
                     "idk": {f"{s:g}": [{"id": int(vid), "scale": s}] for s in idk_scales}})
    for stem, c in (json.loads(Path(args.combos).read_text()) if args.combos else {}).items():
        axis = json.loads((Path(args.voices_dir) / c["slug"] / "axis.json").read_text())
        arms.append({"stem": stem, "kind": "combo", "prompts": axis["test_prompts"], "sweep": c["settings"],
                     "idk": c["settings"]})
    for spec in args.instruction:
        stem, slug = spec.split("=")
        axis = json.loads((Path(args.voices_dir) / slug / "axis.json").read_text())
        sentence = f"Reply in this voice: {axis['description'].strip()}"
        arms.append({"stem": stem, "kind": "instruction", "prompts": axis["test_prompts"], "system": sentence,
                     "sweep": {"instruction": []}, "idk": {"instruction": []}})
    if args.presets:
        dial_ids = {d.split("=")[0]: int(d.split("=")[1]) for d in args.dial}
        day_axes = {a["name"]: a for a in json.loads(Path(args.axes).read_text())["axes"]}
        prompts = day_axes["concise"]["test_prompts"][:3] + day_axes["formal"]["test_prompts"][:3] + \
            day_axes["decisive"]["test_prompts"][:2]
        for p in json.loads(Path(args.presets).read_text())["presets"]:
            if not p["dials"] or any(d["axis"] not in dial_ids for d in p["dials"]):
                continue
            cv = [{"id": dial_ids[d["axis"]], "scale": d["scale"]} for d in p["dials"]]
            arms.append({"stem": f"preset-{p['slug']}", "kind": "preset", "prompts": prompts, "dials": p["dials"],
                         "sweep": {"preset": cv}, "idk": {"preset": cv}})

    if "sweep" in args.steps:
        for arm in arms:
            settings = {"0": [], **arm["sweep"]}
            jobs = [(label, p) for label in settings for p in arm["prompts"]]
            texts = dict(zip(arm["prompts"], pool.map(lambda p: answer_prompt(ep, p), arm["prompts"])))
            sys_texts = dict(zip(arm["prompts"], pool.map(lambda p: answer_prompt(ep, p, arm["system"]), arm["prompts"]))) \
                if arm.get("system") else texts
            res = list(pool.map(lambda j: complete(ep, (texts if j[0] == "0" else sys_texts)[j[1]], settings[j[0]],
                                                   args.max_tokens), jobs))
            by_scale = {}
            for (label, p), r in zip(jobs, res):
                by_scale.setdefault(label, []).append({"prompt": p, **r})
            summary = {k: summarize(v) for k, v in by_scale.items()}
            (out / f"sweep-{arm['stem']}.json").write_text(json.dumps(
                {"voice": arm["stem"], "kind": arm["kind"], "dials": arm.get("dials"), "system": arm.get("system"),
                 "settings": settings,
                 "summary": summary, "replies": by_scale}, indent=1, ensure_ascii=False) + "\n")
            print(json.dumps({"sweep": arm["stem"], "summary": summary, "elapsed_s": round(time.time() - t0)}), flush=True)
    if "idk" in args.steps:
        items = simpleqa_gate.gate_items()
        gold = {q["id"]: q["answer"] for q in items}
        prompts = list(pool.map(lambda q: answer_prompt(ep, simpleqa_gate.PROMPT.format(question=q["question"])), items))

        def run(cv, system=None):
            ps = prompts if not system else list(pool.map(
                lambda q: answer_prompt(ep, simpleqa_gate.PROMPT.format(question=q["question"]), system), items))
            ans = list(pool.map(lambda p: complete(ep, p, cv, simpleqa_gate.MAX_NEW_TOKENS), ps))
            return [{"id": q["id"], **a} for q, a in zip(items, ans)]
        base_rows = run([])
        base = idk_stats(base_rows, gold)
        (out / "idk-base.json").write_text(json.dumps({"set": SET, "control_vectors": [], "stats": base,
                                                       "answers": base_rows}, indent=1, ensure_ascii=False) + "\n")
        print(json.dumps({"idk": "base", **base, "elapsed_s": round(time.time() - t0)}), flush=True)
        for arm in arms:
            doc = {"voice": arm["stem"], "kind": arm["kind"], "set": SET, "base": base, "by_scale": {}}
            for label, cv in arm["idk"].items():
                rows = run(cv, arm.get("system"))
                st = {**idk_stats(rows, gold), **flips(base_rows, rows)}
                doc["by_scale"][label] = {**st, "control_vectors": cv, "answers": rows}
                print(json.dumps({"idk": arm["stem"], "setting": label, **st, "elapsed_s": round(time.time() - t0)}), flush=True)
            (out / f"idk-{arm['stem']}.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    if "known" in args.steps:
        items = [json.loads(line) for f in KNOWN for line in f.read_text().splitlines() if line.strip()]
        gold = {q["id"]: q["gold"] for q in items}
        prompts = list(pool.map(lambda q: answer_prompt(ep, simpleqa_gate.PROMPT.format(question=q["question"])), items))

        def run_known(cv, system=None):
            ps = prompts if not system else list(pool.map(
                lambda q: answer_prompt(ep, simpleqa_gate.PROMPT.format(question=q["question"]), system), items))
            ans = list(pool.map(lambda p: complete(ep, p, cv, simpleqa_gate.MAX_NEW_TOKENS), ps))
            return [{"id": q["id"], **a} for q, a in zip(items, ans)]
        base_rows = run_known([])
        base = known_stats(base_rows, gold)
        (out / "known-base.json").write_text(json.dumps({"set": "known slice v1+v2 (200), gate prompt form", "stats": base,
                                                         "answers": base_rows}, indent=1, ensure_ascii=False) + "\n")
        print(json.dumps({"known": "base", **base, "elapsed_s": round(time.time() - t0)}), flush=True)
        for arm in arms:
            doc = {"voice": arm["stem"], "kind": arm["kind"], "set": "known slice v1+v2 (200), gate prompt form",
                   "grading": "right = a normalised gold answer is a substring of the normalised reply", "base": base,
                   "by_scale": {}}
            for label, cv in arm["idk"].items():
                rows = run_known(cv, arm.get("system"))
                st = known_stats(rows, gold)
                st["lost"] = sum(known_right(b["answer"], gold[b["id"]]) and not known_right(r["answer"], gold[r["id"]])
                                 for b, r in zip(base_rows, rows))
                doc["by_scale"][label] = {**st, "control_vectors": cv, "answers": rows}
                print(json.dumps({"known": arm["stem"], "setting": label, **st, "elapsed_s": round(time.time() - t0)}), flush=True)
            (out / f"known-{arm['stem']}.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
