#!/usr/bin/env python3
"""META-AUTO-REASONING direction screen (owner design, 29 Oct): one pass, four labeled options.

The owner's words: "how about it just has the 2 options in the instant case? A or B? option 1: A option 2: B option 3:
ask option 4: think more/complicated." Each pair decision (duplicate, supersede) is asked once with four options: the
two answers, "ask the user" and "think it through". Glimmer picks escalation itself in that single pass. Reasoning
runs only when it picks "think": that reasoned call is the plain two-option question with reasoning on (the D3 call),
and its pick between the two answers is final; a reasoning turn that ends at its cap is undecided. "Ask" is neither
right nor wrong and is counted apart. The comparison is plain one-pass A/B on the same rows, same server, same arm.

  build   the 244 pairs of the D3 run (ids from readout-D3.jsonl; rows and code-computed labels from decide/data)
          -> decide/data/auto4/pairs.jsonl with the plain and the four-option prompt
  run     --phase onepass: per row, the four-option call and the plain call (one token each)
          --phase reason:  the reasoned call for every row whose four-option pick is "think"
  score   per arm: picks, end result (right / wrong / asked / undecided), accuracy on decided rows, latency;
          beside plain one-pass and plain with "ask below p 0.9"
"""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "decide"))
import prompts  # noqa: E402

DATA = ROOT / "decide" / "data"
OUT_DIR = ROOT / "eval" / "results" / "2026-10-29-decision"
PAIRS = DATA / "auto4" / "pairs.jsonl"
SOURCES = ["supersede-day2-check", "supersede-test", "duplicate-test", "duplicate-calibration", "supersede-calibration"]
ASK, THINK = "3", "4"
PLAIN_ASK_BELOW = 0.9
REASONING_CAP = 1024  # = readout.REASONING_MAX_TOKENS; readouts written before 29 Oct 04:41 carry no "undecided"


def read_jsonl(path):
    path = Path(path)
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def build(args):
    ids = [r["id"] for r in read_jsonl(args.ids_from)]
    rows = {r["id"]: r for name in SOURCES for r in read_jsonl(DATA / f"{name}.jsonl")}
    missing = [i for i in ids if i not in rows]
    if missing:
        raise SystemExit(f"{len(missing)} ids not in decide/data: {missing[:3]}")
    PAIRS.parent.mkdir(parents=True, exist_ok=True)
    with PAIRS.open("w") as f:
        for i in ids:
            r = dict(rows[i])
            if r["prompt"] != prompts.pair_prompt(r["kind"], r["a"], r["b"]):
                raise SystemExit(f"{i}: frozen prompt differs from decide/prompts.py")
            r["prompt4"] = prompts.pair_prompt_auto4(r["kind"], r["a"], r["b"])
            r["options4"] = prompts.pair_options_auto4(r["kind"])
            r["prompt4_id"] = prompts.AUTO4_PROMPT_IDS[r["kind"]]
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(ids)} rows -> {PAIRS.relative_to(ROOT)}")


def one_pass(readout, server, glimmer, text, labels, accept, lora, slot):
    ids = glimmer.ids(glimmer.render(text))
    dist, mass, top, ms, r = readout.label_probs(server, ids, glimmer.label_ids(labels), lora, slot)
    t = r.get("timings") or {}
    pick = max(dist, key=dist.get)
    return {"pick": pick, "p": round(dist[pick], 4), "right": pick in accept, "label_mass": round(mass, 4), "ms": ms,
            "server_ms": round((t.get("prompt_ms") or 0) + (t.get("predicted_ms") or 0), 1), "prompt_tokens": len(ids),
            "dist": {k: round(v, 4) for k, v in dist.items()}, "top": top}


def run(args):
    import readout

    glimmer = readout.Glimmer(args.model_dir)
    lora = readout.lora_scales(args.server, args.adapter)
    rows = read_jsonl(PAIRS)
    out = OUT_DIR / f"readout-auto4-{args.arm}-{args.phase}.jsonl"
    done = {r["id"] for r in read_jsonl(out)}
    if args.phase == "reason":
        picks = {r["id"]: r["auto4"]["pick"] for r in read_jsonl(OUT_DIR / f"readout-auto4-{args.arm}-onepass.jsonl")}
        rows = [r for r in rows if picks.get(r["id"]) == THINK]
    print(f"{args.arm} {args.phase}: {len(rows)} rows, {len(done)} done, lora {lora}", flush=True)
    with out.open("a") as f:
        for row in rows:
            if row["id"] in done:
                continue
            if (args.stop_file and Path(args.stop_file).exists()) or (args.until and time.strftime("%H:%M") >= args.until):
                print("stopped before", row["id"], flush=True)
                return
            res = {"id": row["id"], "kind": row["kind"], "split": row["split"], "label": row["label"], "arm": args.arm}
            if args.phase == "onepass":
                res["auto4"] = one_pass(readout, args.server, glimmer, row["prompt4"], list(row["options4"]), row["accept"], lora, args.slot)
                res["plain"] = one_pass(readout, args.server, glimmer, row["prompt"], list(row["options"]), row["accept"], lora, args.slot)
                line = (f"4opt {res['auto4']['pick']} p {res['auto4']['p']:.3f}  plain {res['plain']['pick']} "
                        f"p {res['plain']['p']:.3f}  gold {row['label']}")
            else:
                res["reasoned"] = readout.reasoned(args.server, glimmer, row["prompt"], glimmer.label_ids(list(row["options"])),
                                                   row["accept"], lora, args.slot, args.reasoning)
                e = res["reasoned"]
                line = f"reasoned {e['pick']} p {e['p']:.3f} gold {row['label']} {e['reasoning_tokens']} tok {e['ms']} ms"
            res["lora"] = lora
            f.write(json.dumps(res, ensure_ascii=False) + "\n")
            f.flush()
            print(f"{row['id']:55s} {line}", flush=True)
    print(f"{args.arm} {args.phase} DONE", flush=True)


def pct(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 1) if xs else None


def lat(xs):
    return {"p50_ms": pct(xs, 0.5), "p95_ms": pct(xs, 0.95)}


def end_result(rows):
    """right / wrong / asked / undecided, accuracy on decided rows and on all rows."""
    c = {k: sum(r["end"] == k for r in rows) for k in ("right", "wrong", "asked", "undecided")}
    decided = c["right"] + c["wrong"]
    return {"n": len(rows), **c, "acc_decided": round(c["right"] / decided, 4) if decided else None,
            "acc_all": round(c["right"] / len(rows), 4) if rows else None}


def score_arm(onepass, reasons, rows_by_id):
    """Per row: the plain one-pass result, plain with ask below p 0.9, and the four-option result end to end."""
    per = []
    for r in onepass:
        a4, plain = r["auto4"], r["plain"]
        row = {"id": r["id"], "kind": r["kind"], "split": r["split"], "label": r["label"], "pick4": a4["pick"], "p4": a4["p"],
               "plain_pick": plain["pick"], "plain_p": plain["p"], "plain_right": plain["right"],
               "plain_ms": plain["server_ms"], "onepass4_ms": a4["server_ms"]}
        row["plain_end"] = "right" if plain["right"] else "wrong"
        row["plain_ask_end"] = "asked" if plain["p"] < PLAIN_ASK_BELOW else row["plain_end"]
        e2e = a4["server_ms"]
        if a4["pick"] == ASK:
            row["end"] = "asked"
        elif a4["pick"] == THINK:
            e = reasons.get(r["id"])
            if e is None:
                row["end"] = "not_run"
            else:
                capped = e.get("undecided", e.get("stop") == "limit" or (e.get("reasoning_tokens") or 0) >= REASONING_CAP)
                row["end"] = "undecided" if capped else ("right" if e["right"] else "wrong")
                row["reasoned_pick"], row["reasoned_p"], row["reasoning_tokens"] = e["pick"], e["p"], e["reasoning_tokens"]
                e2e += e["ms"]
        else:
            row["end"] = "right" if a4["right"] else "wrong"
        row["e2e_ms"] = e2e
        # the four-option pass read as an A/B decision (escalation labels dropped)
        ab = {k: a4["dist"][k] for k in rows_by_id[r["id"]]["options"]}
        row["ab_pick"] = max(ab, key=ab.get)
        per.append(row)
    return per


def summarize(per):
    picks = {"answer": sum(r["pick4"] not in (ASK, THINK) for r in per), "ask": sum(r["pick4"] == ASK for r in per),
             "think": sum(r["pick4"] == THINK for r in per)}
    think_rows = [r for r in per if r["pick4"] == THINK and r["end"] != "not_run"]
    plain_wrong = [r for r in per if not r["plain_right"]]
    plain_right = [r for r in per if r["plain_right"]]
    return {
        "n": len(per),
        "plain": {**end_result([{**r, "end": r["plain_end"]} for r in per]),
                  "wrong_at_0.9": sum(r["plain_end"] == "wrong" and r["plain_p"] >= 0.9 for r in per),
                  **lat([r["plain_ms"] for r in per])},
        "plain_ask_below_0.9": end_result([{**r, "end": r["plain_ask_end"]} for r in per]),
        "auto4": {"picks": picks, **end_result([r for r in per if r["end"] != "not_run"]),
                  "not_run": sum(r["end"] == "not_run" for r in per),
                  "direct_wrong_at_0.9": sum(r["end"] == "wrong" and r["pick4"] not in (ASK, THINK) and r["p4"] >= 0.9 for r in per),
                  "think_capped": sum(r["end"] == "undecided" for r in think_rows),
                  "onepass": lat([r["onepass4_ms"] for r in per]), "end_to_end": lat([r["e2e_ms"] for r in per]),
                  "think_rows_end_to_end": lat([r["e2e_ms"] for r in think_rows])},
        # does it escalate where plain one-pass is wrong (catch) or where plain one-pass is right (cost)?
        "escalated_of_plain_wrong": f"{sum(r['pick4'] in (ASK, THINK) for r in plain_wrong)}/{len(plain_wrong)}",
        "escalated_of_plain_right": f"{sum(r['pick4'] in (ASK, THINK) for r in plain_right)}/{len(plain_right)}",
        "ab_pick_equals_plain": f"{sum(r['ab_pick'] == r['plain_pick'] for r in per)}/{len(per)}",
    }


def score(args):
    rows_by_id = {r["id"]: r for r in read_jsonl(PAIRS)}
    report = {"design": "owner META-AUTO-REASONING: 1 = answer A, 2 = answer B, 3 = ask the user, 4 = think it through "
                        "(reasoned two-option call, high, cap 1024 = undecided); ask is neither right nor wrong",
              "prompt_ids": prompts.AUTO4_PROMPT_IDS, "rows": str(PAIRS.relative_to(ROOT)), "arms": {}}
    groups = {"all": lambda r: True, "duplicate": lambda r: r["kind"] == "duplicate",
              "supersede": lambda r: r["kind"] == "supersede",
              **{f"{k}/{s}": (lambda r, k=k, s=s: r["kind"] == k and r["split"] == s)
                 for k, s in sorted({(r["kind"], r["split"]) for r in rows_by_id.values()})}}
    for arm in args.arm:
        onepass = read_jsonl(OUT_DIR / f"readout-auto4-{arm}-onepass.jsonl")
        reasons = {r["id"]: r["reasoned"] for r in read_jsonl(OUT_DIR / f"readout-auto4-{arm}-reason.jsonl")}
        if not onepass:
            continue
        per = score_arm(onepass, reasons, rows_by_id)
        report["arms"][arm] = {g: summarize([r for r in per if f(r)]) for g, f in groups.items() if any(f(r) for r in per)}
        report["arms"][arm]["escalated_rows"] = [r for r in per if r["pick4"] in (ASK, THINK)]
    out = OUT_DIR / "score-auto4.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    print(table(report))
    print(f"-> {out.relative_to(ROOT)}")


def table(report):
    lines = ["| arm | method | answer / ask / think | right | wrong | asked | undecided | acc decided | acc all | p50 / p95 ms |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for arm, groups in report["arms"].items():
        s = groups["all"]
        pl, pa, a4 = s["plain"], s["plain_ask_below_0.9"], s["auto4"]
        lines.append(f"| {arm} | plain A/B one pass | {s['n']} / 0 / 0 | {pl['right']} | {pl['wrong']} | 0 | 0 | "
                     f"{pl['acc_decided']} | {pl['acc_all']} | {pl['p50_ms']} / {pl['p95_ms']} |")
        lines.append(f"| {arm} | plain, ask below p 0.9 | {s['n'] - pa['asked']} / {pa['asked']} / 0 | {pa['right']} | {pa['wrong']} | "
                     f"{pa['asked']} | 0 | {pa['acc_decided']} | {pa['acc_all']} | {pl['p50_ms']} / {pl['p95_ms']} |")
        k = a4["picks"]
        lines.append(f"| {arm} | 4 options (owner) | {k['answer']} / {k['ask']} / {k['think']} | {a4['right']} | {a4['wrong']} | "
                     f"{a4['asked']} | {a4['undecided']} | {a4['acc_decided']} | {a4['acc_all']} | "
                     f"{a4['end_to_end']['p50_ms']} / {a4['end_to_end']['p95_ms']} |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--ids-from", default=str(OUT_DIR / "readout-D3.jsonl"))
    r = sub.add_parser("run")
    r.add_argument("--server", default="http://100.88.57.107:18610")
    r.add_argument("--model-dir", default="/Users/olety/models/mlx/Muse-Glimmer-30B-4bit")
    r.add_argument("--arm", required=True, help="base or D1b (names the output files)")
    r.add_argument("--adapter", help="path suffix of the adapter at scale 1 (others at 0); none = base")
    r.add_argument("--phase", choices=["onepass", "reason"], required=True)
    r.add_argument("--slot", type=int, required=True)
    r.add_argument("--reasoning", default="high")
    r.add_argument("--until", help="stop before the next call after this local time, HH:MM")
    r.add_argument("--stop-file")
    s = sub.add_parser("score")
    s.add_argument("--arm", nargs="+", default=["base", "D1b"])
    args = ap.parse_args()
    {"build": build, "run": run, "score": score}[args.cmd](args)


if __name__ == "__main__":
    main()
