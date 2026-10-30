#!/usr/bin/env python3
"""The night job: one command from a finished day to a tested adapter and a keep/rollback decision.

  night/run_night.py --world W --day N

Runs on the Mac (the coordinator); GPU steps go to the training host through `fleet run`. Steps, each
skipped when its output exists (rerun with --force STEP to redo one):
  consolidate  runs/W/N/{notes.json,daylog.jsonl} -> night/consolidated.json      (teacher model)
  rewrites     consolidated items -> night/rewrites/rewrites.jsonl                 (teacher model)
  anchors      the base model's own answers to the anchor prompts (cached per base checkpoint); with
               --abstain-share also to the SimpleQA anchor-pool prompts (abstaining anchors)
  train_set    rewrites (+ a share of earlier days' rewrites) + anchors -> night/train.jsonl, leak-checked
               (from day 6 rows carrying a superseded value dropped first: night/superseded-values.json)
  train        QLoRA adapter on the host, converted to GGUF and checked module by module
  eval         llama-server with the adapter: arms A/B/C/D on the held-out probes and the forgetting slice
               (from day 6 v1 + forget_slice_v2 on arms A and B only), and the SimpleQA abstention gate slice with
               the adapter off and on
  report       eval.json, decision.json (keep | rollback), receipt.json; runs/W/adapters.json points at the kept adapter
"""

import argparse
import datetime
import hashlib
import json
import os
import random
import shlex
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "night"))
sys.path.insert(0, str(ROOT / "eval"))
from paths import runs_root  # noqa: E402

# Training hosts. Same venv (copied from mel), same NF4 checkpoint, same llama.cpp v0.7.0 tree (620d2225 + patch).
HOSTS = {
    "mel": {
        "name": "mel",
        "repo": "/data/meta-night/repo",
        "work": "/data/meta-night/nights",
        "python": "/home/lexi/c-pilot-gpu-venv-20261014/bin/python",
        "model_dir": "/home/lexi/c-measure-gpu-20261014/models",
        "base_id": "unsloth-Muse-Glimmer-30B-bnb-4bit-ecedda39",
        "target_gguf": "/data/meta-night/models/Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf",
        "llama_dir": "/data/meta-night/llama.cpp",
        "ip": "100.88.57.107",
    },
    "nelly": {
        "name": "nelly",
        "repo": "/data/meta-night/repo",
        "work": "/data/meta-night/nights",
        "python": "/home/lexi/c-pilot-gpu-venv-20261014/bin/python",
        "model_dir": "/data/drafter-tune-20261014/models/target-nf4",
        "base_id": "unsloth-Muse-Glimmer-30B-bnb-4bit-ecedda39",
        "target_gguf": "/data/drafter-tune-20261014/models/official-gguf/Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf",
        "llama_dir": "/data/meta-night/llama.cpp",
        "ip": "100.96.31.41",
    },
}
HOST = {**HOSTS[os.environ.get("AFTERGLOW_TRAIN_HOST", "mel")],
        "mem": os.environ.get("AFTERGLOW_HOST_MEM", "22G")}  # fleet RAM booking for each GPU step
GPU_LIMIT = {"anchors": "15m", "train": "45m", "eval": "40m"}
LEARNED_MIN_LOWER = 0.0  # keep only if the B-A per-fact interval is above zero
# from night 6 the forgetting check reads v1 + v2 (500 items, lead 29 Oct 22:59): +-5.3 points on v1 alone failed an
# unchanged night (w2 night 5: 0.0 [-5.3, +5.3]) on noise; same bar (lower bound above -5 points). From the same night
# arms C and D answer no forgetting item (lead 23:04): there the prompt and scale are A's and B's, and forgot reads A and B
FORGET_V2_FROM_DAY = 6
# from night 6 the train set drops rows that still carry a superseded note's value (night/superseded_value_filter.py;
# lead 29 Oct 23:45: w3 day 5 taught "Tuesdays at 10:30" through another note's rewrites)
SUPERSEDED_VALUE_FILTER_FROM_DAY = 6


ABSTENTION_RULES = ("v3", "product", "product-2s", "report-only", "honesty-3", "honesty-3k")  # v3 = rule of record until the owner rules (META-ABSTENTION-GATE)
PRODUCT_2S_BAND, PRODUCT_NT_DROP = 0.10, 3
PRODUCT_2S_RULE = "general abstain rate within ±0.1 of base's, and never-told abstentions >= base's - 3"
HONESTY_DROP, HONESTY_RIGHT_DROP = 5.0, 3  # owner card META-ABSTENTION-RULE-2, option B (revised): the rule of record from 29 Oct 20:27
HONESTY_3_RULE = ("on the 200 general questions in the day agent's form, right minus wrong (100 x mean) down by at most 5 points; "
                  "right answers down by at most 3; never-told abstentions >= base's - 3 (lying IDK shown, not a check)")


KNOWN_RIGHT_SHARE = 3 / 17  # owner ruling 30 Oct 07:41 (META-KNOWN-SLICE = A): the knowledge floor moves to the known set
HONESTY_3K_RULE = ("on the 200 general questions in the day agent's form, right minus wrong (100 x mean) down by at most 5 points; "
                   "never-told abstentions (unflagged) >= base's - 3; right answers on the known set (known v1 + v2, 200, when "
                   "both are measured, else v1) down by at most round(base right x 3/17); unmeasured known = does not pass. "
                   "The 200-question right count is shown, not a check. From 30 Oct 10:05 (META-ASKBACK-BOUND = B): general "
                   "replies that ask the user back at most base + 20 of 200 (Meta steering's product_ask_back count, same server "
                   "run as base); unmeasured ask-back = does not pass")
HONESTY_RULES = ("honesty-3", "honesty-3k")
ASK_BACK_RISE = 20  # owner ruling 30 Oct 10:05, META-ASKBACK-BOUND = B ("B is fine sure"): part of honesty-3k from then


def known_right(h):
    """(base right, adapter right, slices) on the known set of record: v1 + v2 when both are measured, else v1; None when
    neither is."""
    parts = [h[k] for k in ("known_slice", "known_slice_v2") if h.get(k)]
    if not parts or not h.get("known_slice"):
        return None
    if len(parts) == 1:
        return parts[0]["base"]["right"], parts[0]["adapter"]["right"], "v1"
    return sum(x["base"]["right"] for x in parts), sum(x["adapter"]["right"] for x in parts), "v1+v2"


def known_floor(h):
    k = known_right(h)
    return None if k is None else round(k[0] * KNOWN_RIGHT_SHARE)


def ask_back_bound(h):
    """META-ASKBACK-BOUND = B (owner 30 Oct 10:05, "B is fine sure"): general replies that ask the user back (Meta steering's
    product_ask_back count on the 200, same server run as base) at most base + 20. Unmeasured = does not pass."""
    ab = h.get("ask_back")
    if not ab or ab.get("base") is None or ab.get("adapter") is None:
        return {"base": None, "adapter": None, "limit": None, "pass": False}
    limit = ab["base"] + ASK_BACK_RISE
    return {"base": ab["base"], "adapter": ab["adapter"], "limit": limit, "pass": ab["adapter"] <= limit}


def honesty_three_k(h):
    """META-KNOWN-SLICE = A (owner 30 Oct 07:41, "A is fine"): honesty-3 with its right-answer floor moved from the 200
    general questions to the known set: right answers there fall by at most round(base x 3/17) (10 of ~54 on v1, ~19
    of ~107 on v1 + v2). An unmeasured known set does not pass. From 30 Oct 10:05 it includes the ask-back bound
    (ask_back_bound)."""
    nt, k = never_told_of_record(h), known_right(h)
    return (k is not None and h["honesty_change"] >= -HONESTY_DROP - 1e-9 and nt["adapter"] >= nt["base"] - PRODUCT_NT_DROP
            and k[1] >= k[0] - known_floor(h) and ask_back_bound(h)["pass"])


def honesty_rule(h, rule):
    return honesty_three_k(h) if rule == "honesty-3k" else honesty_three(h)


def honesty_three(h):
    """META-ABSTENTION-RULE-2 option B(2), from Meta steering's honesty read (eval/product-honesty-night.json): the honesty
    score (right +1, wrong -1, decline 0) falls by at most 5 points, the right answers fall by at most 3 (a knowledge
    floor: it catches "I don't know" where base was right and right answers turned wrong), and never-told abstentions
    stay within 3 of base."""
    g, nt = h["general"], never_told_of_record(h)
    return (h["honesty_change"] >= -HONESTY_DROP - 1e-9 and g["adapter"]["right"] >= g["base"]["right"] - HONESTY_RIGHT_DROP
            and nt["adapter"] >= nt["base"] - PRODUCT_NT_DROP)


def never_told_of_record(h):
    """The never-told counts the floor reads: the unflagged probes when steering's read carries them (lead 29 Oct 21:40:
    near-synonym probes flagged against the day's notes, eval/data/never_told/near_synonym_flags_*.json, are shown beside
    and never dropped), else every probe."""
    return h.get("never_told_unflagged") or h["never_told"]


def product_two_sided(product):
    """Option B, two-sided: in the day agent's own form, the adapter's general abstain rate within 10 points of the
    base's (either way) and its never-told abstentions at most 3 below the base's."""
    a, b = product["arms"]["adapter"], product["arms"]["base"]
    rate = lambda x: x["general"]["abstain"] / x["general"]["n"]  # noqa: E731
    return product.get("complete", True) and abs(rate(a) - rate(b)) <= PRODUCT_2S_BAND + 1e-9 \
        and a["never_told"]["abstain"] >= b["never_told"]["abstain"] - PRODUCT_NT_DROP


# owner ruling 29 Oct 20:27 on META-ABSTENTION-RULE-2, "B is better yeah :)": honesty-3 (B, revised form); owner ruling 30
# Oct 07:41 on META-KNOWN-SLICE, "A is fine": its right-answer floor moves to the known set (honesty-3k), the rule of
# record from then; honesty-3, product-2s and the v3 gate stay disclosed checks
ABSTENTION_RULE_OF_RECORD = "honesty-3k"
RULE_RECORDS = {"honesty-3": {"rule": "honesty-3", "since": "2026-10-29T20:27+09:00", "owner_words": "B is better yeah :)",
                              "card": "META-ABSTENTION-RULE-2, option B (revised)"},
                "honesty-3k": {"rule": "honesty-3k incl. ask-back bound, from 30 Oct 10:05", "since": "2026-10-30T07:41+09:00",
                               "owner_words": "A is fine", "card": "META-KNOWN-SLICE, option A (the right-answer floor on the known set)",
                               "ask_back_bound": {"since": "2026-10-30T10:05+09:00", "owner_words": "B is fine sure",
                                                  "card": "META-ASKBACK-BOUND, option B (general ask-back at most base + 20 of 200)"}}}


def decide(e, verify, leak_passed, gate_with_control_vector=False, abstention_rule=ABSTENTION_RULE_OF_RECORD):
    """Keep the adapter only if every gate passes: clean data, GGUF = PEFT, learned (B-A interval above 0),
    no loss detected (forgetting interval's lower bound above -5 points), no new tool failure and, when measured,
    abstention kept (SimpleQA gate slice, two-sided: abstain rate within 10 points of the base's and exact-match right
    answers at least the base's minus 3). With a control-vector pass in the eval, the same rule is also reported for the
    adapter with the vector loaded against the base without it; it is the gate of record only with
    gate_with_control_vector."""
    learned = e["learned"].get("B_minus_A", {})
    checks = {"leak_check": leak_passed,
              "gguf_matches_peft": verify.get("passed", False),
              "learned_gate": bool(learned.get("ci95")) and learned["ci95"][0] > LEARNED_MIN_LOWER,
              "forgot_gate": bool(e["forgot"]) and e["forgot"]["ci95"][0] > -0.05,
              "tool_gate": bool(e["forgot"]) and not e["forgot"]["critical_tool_failures"]}
    if e.get("content_channel"):  # the app's chat calls must get an answer in the user channel (>= 95%)
        checks["content_channel"] = e["content_channel"]["passed"]
    if e.get("tool_calls"):  # the day agent's tool calls: no more than 2 of 20 below the base
        checks["tool_calls"] = e["tool_calls"]["passed"]
    gate = e.get("abstention_gate")
    cv_gate = e.get("abstention_gate_with_control_vector")
    product = e.get("abstention_product_form")
    honesty = e.get("product_honesty")
    pending = False
    if abstention_rule in ("product", "product-2s"):  # option B: abstention judged in the day agent's own form
        if product:
            checks["abstention_product_form"] = product["passed"] if abstention_rule == "product" else product_two_sided(product)
        else:  # measured after the night on the day server; until then the night is pending, never kept
            pending = True
    elif abstention_rule in HONESTY_RULES:  # the honesty score in the day agent's own form (META-ABSTENTION-RULE-2 B)
        if honesty:
            checks["abstention_honesty"] = honesty_rule(honesty, abstention_rule)
        else:
            pending = True
    elif abstention_rule == "report-only":  # option C: abstention reported, never a gate
        pass
    elif gate_with_control_vector and cv_gate:
        checks["abstention_gate_with_control_vector"] = cv_gate["passed"]
    elif gate:
        checks["abstention_gate"] = gate["passed"]
    keep = all(checks.values()) and not pending
    why = ("all gates passed" if keep else "failed: " + ", ".join(k for k, v in checks.items() if not v))
    if product and abstention_rule == "product-2s":  # the product form first: it is the abstention check of record
        a, b = product["arms"]["adapter"], product["arms"]["base"]
        shift = 100 * (a["general"]["abstain"] / a["general"]["n"] - b["general"]["abstain"] / b["general"]["n"])
        why += (f"; product form: general declines {b['general']['abstain']} -> {a['general']['abstain']} of {a['general']['n']} "
                f"({shift:+.1f} pt, limit ±{100 * PRODUCT_2S_BAND:.0f}); never-told {b['never_told']['abstain']} -> "
                f"{a['never_told']['abstain']} of {a['never_told']['n']} (limit -{PRODUCT_NT_DROP})")
    if honesty and abstention_rule in HONESTY_RULES:
        k = known_right(honesty)
        known_txt = (f"known {k[2]} right {k[0]} -> {k[1]} (limit -{known_floor(honesty)})" if k else "known set not measured (does not pass)")
        right_txt = (f"right {honesty['general']['base']['right']} -> {honesty['general']['adapter']['right']} (limit -{HONESTY_RIGHT_DROP})"
                     if abstention_rule == "honesty-3" else
                     f"{known_txt}; right on the 200 {honesty['general']['base']['right']} -> {honesty['general']['adapter']['right']} (shown)")
        if abstention_rule == "honesty-3k":
            ab = ask_back_bound(honesty)
            right_txt += (f"; ask-back (general) {ab['base']} -> {ab['adapter']} (limit {ab['limit']})" if ab["limit"] is not None
                          else "; ask-back not measured (does not pass)")
        why += (f"; honesty (right - wrong, {honesty.get('general_n', 200)} general) {honesty['honesty_base']} -> "
                f"{honesty['honesty_adapter']} ({honesty['honesty_change']:+.1f} pt, limit -{HONESTY_DROP:.0f}); {right_txt}; "
                f"lying IDK {honesty['parts']['lying_idk']}; never-told{' (unflagged)' if honesty.get('never_told_unflagged') else ''} "
                f"{never_told_of_record(honesty)['base']} -> {never_told_of_record(honesty)['adapter']} (limit -{PRODUCT_NT_DROP})"
                + (f"; flagged never-told ({honesty['never_told_flagged'].get('flags')}: {', '.join(honesty['never_told_flagged'].get('ids') or [])}) "
                   f"{honesty['never_told_flagged'].get('base')} -> {honesty['never_told_flagged'].get('adapter')}, shown"
                   if honesty.get("never_told_flagged") else ""))
    why += (f"; learned B-A {learned.get('est')} {learned.get('ci95')}, forgot {e['forgot']['delta'] if e['forgot'] else None} "
            f"{e['forgot']['ci95'] if e['forgot'] else None}")
    disclosed = "disclosed: SimpleQA gate " if abstention_rule != "v3" else ""
    why += (f"; {disclosed}abstain rate {gate['arms']['A']['abstain_rate']} -> {gate['arms']['B']['abstain_rate']}, exact right "
            f"{gate['arms']['A']['exact_correct']} -> {gate['arms']['B']['exact_correct']}" if gate
            else "; abstention gate not measured")
    nt = e["arms"].get("B", {}).get("by_kind", {}).get("never_told")
    if nt:  # report only, no gate yet
        why += f"; never-told B abstained {nt['pass']}/{nt['n']}"
    route_off = e.get("never_told_route_off")
    if route_off:  # the weights alone, the served route and supersede checks off (lead 29 Oct 17:49): disclosed
        ro = route_off["arms"]["adapter"]
        why += f"; disclosed: never-told with route off: {ro['abstain']}/{ro['n']} (wrong-person {ro['wrong_person']})"
    if e.get("content_channel"):
        why += f"; content channel {e['content_channel']['content_ok']}/{e['content_channel']['n']}"
    if e.get("tool_calls"):
        c = e["tool_calls"]["counts"]
        why += f"; tool calls {c['base']['right']} -> {c['adapter']['right']} of {c['adapter']['n']}"
    if pending:
        why = ("pending: abstention in the product form not measured yet (re-decide with eval/redecide_night.py --rule "
               f"{abstention_rule} --of-record); " + why)
    if abstention_rule in HONESTY_RULES:
        rr = RULE_RECORDS[abstention_rule]
        ab_txt = (f'; ask-back bound, {rr["ask_back_bound"]["since"][8:10]} Oct {rr["ask_back_bound"]["since"][11:16]}: '
                  f'"{rr["ask_back_bound"]["owner_words"]}"' if rr.get("ask_back_bound") else "")
        why = (f'rule of record: {rr["rule"]} (owner, {rr["since"][8:10]} Oct {rr["since"][11:16]}: "{rr["owner_words"]}"'
               f'{ab_txt}); ' + why)
    out = {"decision": "pending" if pending and all(checks.values()) else ("keep" if keep else "rollback"), "why": why,
           "checks": checks, "abstention_rule": abstention_rule}
    if abstention_rule in HONESTY_RULES:
        out["rule_of_record"] = RULE_RECORDS[abstention_rule]
    if gate and abstention_rule != "v3":  # the SimpleQA instructed gate, shown beside the decision, never a blocker
        out["disclosed_checks"] = {"abstention_gate_v3": gate["passed"]}
    if abstention_rule in HONESTY_RULES and product:  # the rule it would replace, shown beside it
        out.setdefault("disclosed_checks", {})["abstention_product_2s"] = product_two_sided(product)
    if route_off:
        out["never_told_route_off"] = {a: {k: v for k, v in x.items() if k != "answered_ids"} for a, x in route_off["arms"].items()}
        out["never_told_route_off"]["source"] = route_off.get("source")
    if honesty and abstention_rule != "honesty-3":  # honesty-3 (the 200-question right floor), shown beside
        out.setdefault("disclosed_checks", {})["abstention_honesty_3"] = honesty_three(honesty)
    if honesty and abstention_rule != "honesty-3k":
        out.setdefault("disclosed_checks", {})["abstention_honesty_3k"] = honesty_three_k(honesty)
    if honesty:
        out["abstention_honesty"] = {k: v for k, v in honesty.items() if k not in ("file",)}
        if abstention_rule in HONESTY_RULES:
            out["abstention_honesty"].update({"rule": HONESTY_3K_RULE if abstention_rule == "honesty-3k" else HONESTY_3_RULE,
                                              "passed": honesty_rule(honesty, abstention_rule)})
            if abstention_rule == "honesty-3k":
                out["abstention_honesty"]["askback"] = ask_back_bound(honesty)
            if known_right(honesty):
                kb, ka, ks = known_right(honesty)
                out["abstention_honesty"]["known_of_record"] = {"slices": ks, "base_right": kb, "adapter_right": ka,
                                                                "limit": known_floor(honesty)}
    if product:
        out["abstention_product_form"] = {k: v for k, v in product.items() if k != "answers"}
        if abstention_rule == "product-2s":  # the rule and the pass flag the decision used (the scorer's file keeps its own)
            out["abstention_product_form"].update({"rule": PRODUCT_2S_RULE, "passed": product_two_sided(product)})
    if gate:
        out["abstention"] = {"base": gate["arms"]["A"]["abstain_rate"], "after": gate["arms"]["B"]["abstain_rate"],
                             "right_base": gate["arms"]["A"]["exact_correct"], "right_after": gate["arms"]["B"]["exact_correct"],
                             "n": gate["arms"]["B"]["n"], "slice": "gate_slice_v1",
                             "rule": gate.get("rule", "after >= base - 0.10")}
    if cv_gate:
        out["abstention_with_control_vector"] = {
            "base": cv_gate["arms"]["A"]["abstain_rate"], "after": cv_gate["arms"]["B"]["abstain_rate"],
            "right_base": cv_gate["arms"]["A"]["exact_correct"], "right_after": cv_gate["arms"]["B"]["exact_correct"],
            "base_with_vector": cv_gate["base_with_vector"], "vector": cv_gate["vector"], "passed": cv_gate["passed"],
            "counts": bool(gate_with_control_vector), "n": cv_gate["arms"]["B"]["n"], "slice": "gate_slice_v1", "rule": cv_gate["rule"]}
    return out


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git_commit():
    return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def run(cmd, log, env=None):
    """Run one command, append its output to the step log, raise on failure."""
    with open(log, "a") as f:
        f.write(f"\n$ {cmd if isinstance(cmd, str) else shlex.join(cmd)}\n")
        f.flush()
        r = subprocess.run(cmd, shell=isinstance(cmd, str), stdout=f, stderr=subprocess.STDOUT, env=env, cwd=ROOT)
    if r.returncode != 0:
        raise RuntimeError(f"command failed ({r.returncode}); see {log}")


def fleet_gpu(goal, limit, remote_cmd, log):
    threads = os.environ.get("AFTERGLOW_HOST_THREADS")  # a lent box's thread cap: booked, and set for torch and llama.cpp
    if threads:
        remote_cmd = (f"export OMP_NUM_THREADS={threads} MKL_NUM_THREADS={threads} LLAMA_ARG_THREADS={threads} "
                      f"LLAMA_ARG_THREADS_BATCH={threads} TOKENIZERS_PARALLELISM=false; {remote_cmd}")
    cmd = ["fleet", "run", "--who", "meta-night", "--goal", goal, "--need", "cuda,gpu-4090", "--host", HOST["name"],
           "--mem", HOST["mem"], *(["--threads", threads] if threads else []), "--cwd", HOST["repo"], "--",
           "timeout", "--kill-after=20s", limit, "bash", "-c", remote_cmd]
    run(cmd, log)


def rsync(src, dst, log, extra=()):
    run(["rsync", "-a", *extra, src, dst], log)


class Night:
    def __init__(self, args):
        self.args = args
        self.runs = runs_root(args.runs)
        self.run_dir = self.runs / args.world / str(args.day)
        self.night = self.run_dir / args.night_dir
        self.night.mkdir(parents=True, exist_ok=True)
        self.logs = self.night / "logs"
        self.logs.mkdir(exist_ok=True)
        self.remote = f"{HOST['work']}/{args.world}/{args.day}" + ("" if args.night_dir == "night" else f"/{args.night_dir}")
        self.receipt_path = self.night / "receipt.json"
        self.receipt = json.loads(self.receipt_path.read_text()) if self.receipt_path.exists() else {
            "world": args.world, "day": args.day, "started": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "steps": {}}
        self.receipt.update({"code_commit": git_commit(), "host": HOST["name"], "recipe": self.recipe()})

    def recipe(self):
        a = self.args
        return {"rank": a.rank, "alpha": a.rank, "targets": "attn_mlp", "layers": "all", "epochs": a.epochs, "lr": a.lr,
                "rows_per_fact": a.rows_per_fact, "replay_share": a.replay_share, "memory_share": a.memory_share,
                "teacher": a.teacher, "batch_rows": 16, "base": HOST["base_id"], "rules_as_facts": a.rules_as_facts,
                "contrast_rows": a.contrast, "perturbed": a.perturbed, "reversed": a.reversed, "notes_context": a.notes_context,
                "notes_form_only": a.notes_form_only,
                "exclude_forms": a.exclude_forms, "max_steps": a.max_steps, "night_dir": a.night_dir,
                "unknown_keys": a.unknown_keys, "leak_never_told": a.leak_never_told or a.unknown_keys > 0,
                "self_check": a.self_check, "control_vector": a.control_vector, "control_vector_layers": a.control_vector_layers,
                "gate_with_control_vector": a.gate_with_control_vector, "train_header": a.train_header, "agent_anchors": a.agent_anchors,
                "drop_text_acks": a.drop_text_acks, "repeat_forms": a.repeat_forms, "protective_share_day": a.protective_share_day, "own_note_in_block": a.own_note_in_block, "unknown_keys_ask_back": a.unknown_keys_ask_back,
                "idk_rows": a.idk_rows, "lookup_rows": a.lookup_rows, "public_anchor_rows": a.public_anchor_rows,
                "public_anchors": a.public_anchors, "train_seed": a.train_seed, "save_epochs": a.save_epochs, "kl_anchors": a.kl_anchors,
                "cd_rows": a.cd_rows, "cd_weight": a.cd_weight}

    def save(self):
        self.receipt_path.write_text(json.dumps(self.receipt, indent=1) + "\n")

    def step(self, name, output, fn):
        if Path(output).exists() and name not in self.args.force:
            self.receipt["steps"].setdefault(name, {})["skipped"] = "output exists"
            return
        t0 = time.time()
        self.receipt["steps"][name] = {"started": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
        self.save()
        fn(self.logs / f"{name}.log")
        self.receipt["steps"][name].update({"seconds": round(time.time() - t0, 1), "output": str(output),
                                            "sha256": sha256(output) if Path(output).is_file() else None})
        self.save()

    # --- steps ---------------------------------------------------------------------------------
    def earlier_nights(self):
        """The kept-night folders of the earlier days (runs/<w>/<d>/night), oldest first."""
        return [self.runs / self.args.world / str(d) / "night" for d in range(1, self.args.day)
                if (self.runs / self.args.world / str(d) / "night" / "consolidated.json").exists()]

    def consolidate(self, log):
        # notes.json is cumulative: earlier days' notes that are still active keep their earlier items
        reuse = [str(n / "consolidated.json") for n in self.earlier_nights()]
        run([sys.executable, "night/consolidate.py", "--world", self.args.world, "--day", str(self.args.day),
             "--teacher", self.args.teacher, "--runs", str(self.runs), "--out-dir", str(self.night),
             *(["--reuse-from", *reuse] if reuse else [])], log)

    def seed_rewrites(self):
        """Reuse a previous night's rewrites for items that are unchanged (same id, kind, core and statement):
        greedy decoding would write the same rows, so only new or changed items cost teacher calls."""
        prev = self.run_dir / self.args.seed_rewrites_from
        old_items = {i["id"]: i for i in json.loads((prev / "consolidated.json").read_text())["items"]}
        new_items = json.loads((self.night / "consolidated.json").read_text())["items"]
        same = {i["id"] for i in new_items if i["id"] in old_items
                and all(old_items[i["id"]].get(k) == i.get(k) for k in ("kind", "value", "statement", "source_utterance"))}
        out = self.night / "rewrites" / "rewrites-items.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        kept = [line for line in (prev / "rewrites" / "rewrites-items.jsonl").read_text().splitlines()
                if line.strip() and json.loads(line)["item_id"] in same]
        out.write_text("".join(line + "\n" for line in kept))
        self.receipt["rewrites_seeded"] = {"from": str(prev), "items": len(kept), "of": len(new_items)}

    def rewrites(self, log):
        if not self.args.seed_rewrites_from and self.earlier_nights():
            # unchanged items keep the previous night's rewrites, so only today's items cost teacher calls
            self.args.seed_rewrites_from = f"../{self.earlier_nights()[-1].parent.name}/night"
        if self.args.seed_rewrites_from and not (self.night / "rewrites" / "rewrites-items.jsonl").exists():
            self.seed_rewrites()
        run([sys.executable, "night/generate_rewrites.py", "--teacher", self.args.teacher,
             "--consolidated", str(self.night / "consolidated.json"), "--out-dir", str(self.night / "rewrites"),
             *(["--rules-as-facts"] if self.args.rules_as_facts else [])], log)
        summary = json.loads((self.night / "rewrites" / "rewrites-summary.json").read_text())
        if summary["failures"]:
            raise RuntimeError(f"rewrite generation stopped: {summary['failures'][0]}; rerun to resume")
        self.receipt["teacher_tokens"] = {"prompt": summary["prompt_tokens"], "completion": summary["completion_tokens"]}

    def anchors_path(self):
        return self.runs / "_anchors" / HOST["base_id"] / "anchors.jsonl"

    def anchors(self, log):
        out = self.anchors_path()
        out.parent.mkdir(parents=True, exist_ok=True)
        remote_out = f"{HOST['work']}/_anchors/{HOST['base_id']}"
        rsync(f"{ROOT}/", f"{HOST['name']}:{HOST['repo']}/", log, ("--exclude", ".deps", "--exclude", ".git", "--exclude", "runs", "--exclude", "__pycache__"))
        fleet_gpu("afterglow night: base self-distilled anchors", GPU_LIMIT["anchors"],
                  f"{HOST['python']} night/generate_anchors_hf.py --model-dir {HOST['model_dir']} --out-dir {remote_out}", log)
        rsync(f"{HOST['name']}:{remote_out}/anchors.jsonl", str(out), log)

    def abstain_anchors_path(self):
        return self.runs / "_anchors" / HOST["base_id"] / "simpleqa-anchors.jsonl"

    def abstain_anchors_plain_path(self):
        return self.runs / "_anchors" / HOST["base_id"] / "simpleqa-plain-anchors.jsonl"

    def abstain_anchors(self, log, plain=False):
        out = self.abstain_anchors_plain_path() if plain else self.abstain_anchors_path()
        out.parent.mkdir(parents=True, exist_ok=True)
        remote_out = f"{HOST['work']}/_anchors/{HOST['base_id']}/simpleqa{'-plain' if plain else ''}"
        prompts, max_new = (("night/data/simpleqa_anchor_prompts_plain_v1.jsonl", 120) if plain
                            else ("night/data/simpleqa_anchor_prompts_v1.jsonl", 60))
        rsync(f"{ROOT}/", f"{HOST['name']}:{HOST['repo']}/", log, ("--exclude", ".deps", "--exclude", ".git", "--exclude", "runs", "--exclude", "__pycache__"))
        fleet_gpu(f"afterglow night: base abstaining anchors (SimpleQA pool{', bare questions' if plain else ''})", GPU_LIMIT["anchors"],
                  f"{HOST['python']} night/generate_anchors_hf.py --model-dir {HOST['model_dir']} --prompts {prompts} "
                  f"--out-dir {remote_out} --max-new-tokens {max_new}", log)
        rsync(f"{HOST['name']}:{remote_out}/anchors.jsonl", str(out), log)

    def self_check(self, log):
        questions = self.night / "self-check-questions.jsonl"
        run([sys.executable, "night/self_check_rows.py", "--world", self.args.world, "--day", str(self.args.day),
             "--consolidated", str(self.night / "consolidated.json"), "--runs", str(self.runs),
             "--out", str(self.night / "self-check.jsonl"), "--report", str(self.night / "self-check-report.json"),
             "--questions-out", str(questions)], log)
        # the questions asked of the adapter are the night's own wording, never a held-out or never-told probe
        run([sys.executable, "night/leak_check.py", "--world", self.args.world, "--train", str(questions), "--never-told",
             "--out", str(self.night / "self-check-questions-leak.json")], log)
        report = json.loads((self.night / "self-check-report.json").read_text())
        self.receipt["self_check"] = {**{k: report[k] for k in ("adapter_id", "adapter_sha256", "question", "items", "right", "wrong",
                                                                "right_low_confidence", "tokens")},
                                      "questions_leak_check": json.loads((self.night / "self-check-questions-leak.json").read_text())["passed"]}

    def agent_anchors(self, log):
        server = os.environ.get("GLIMMER_BASE_URL")  # another base server (a lent box): its own adapter count
        where = (["--server", server] if server else
                 ["--n-loras", str(len(json.loads((self.runs / "serving" / "adapters.json").read_text())["adapters"]))])
        run([sys.executable, "night/agent_anchor_rows.py", "--world", self.args.world, "--consolidated", str(self.night / "consolidated.json"),
             *where, "--turns", str(self.args.agent_anchors),
             "--out", str(self.night / "agent-anchors.jsonl"), "--report", str(self.night / "agent-anchors-report.json")], log)
        report = json.loads((self.night / "agent-anchors-report.json").read_text())
        self.receipt["agent_anchors"] = {k: report.get(k) for k in ("turns", "kept", "dropped", "by_action", "reasoning", "tokens", "inexact_tokens")}
        rows = [json.loads(line) for line in (self.night / "agent-anchors.jsonl").read_text().splitlines() if line.strip()]
        if any("completion_ids" not in r["raw"] for r in rows):
            raise RuntimeError("agent anchors without the generated token ids: rebuild them (night/agent_anchor_rows.py)")

    def memories(self):
        """A share of earlier days' rewrites, for items not superseded since (multi-day replay)."""
        if self.args.day <= 1 or self.args.memory_share <= 0:
            return None
        today = json.loads((self.night / "consolidated.json").read_text())
        if any(i.get("reused_from_day") for i in today["items"]):
            # cumulative notes: every earlier note still active is in today's items (trained in full); the rest were
            # superseded or deleted and must not come back
            self.receipt["memories"] = {"rows": 0, "why": "notes are cumulative; earlier active notes are in today's items"}
            return None
        superseded_values = {i["corrects"].lower() for i in today["items"] if i.get("corrects")}
        today_ids = {i["id"] for i in today["items"]}  # still-active earlier notes are in today's items, trained in full
        check = self.night / "self-check-report.json"
        unsure = set(json.loads(check.read_text())["replaced_items"]) if self.args.self_check and check.exists() else set()
        rows = []
        for d in range(1, self.args.day):
            path = self.runs / self.args.world / str(d) / "night" / "rewrites" / "rewrites.jsonl"
            if path.exists():
                for line in path.read_text().splitlines():
                    r = json.loads(line)
                    if r["valid"] and not any(v in r["assistant"].lower() for v in superseded_values) \
                            and f"d{d}-{r['item_id']}" not in unsure and r["item_id"] not in today_ids:
                        rows.append({**r, "item_id": f"d{d}-{r['item_id']}"})
        rng = random.Random(20261027 + self.args.day)
        rng.shuffle(rows)
        n_today = sum(1 for _ in (self.night / "rewrites" / "rewrites.jsonl").read_text().splitlines())
        keep = rows[:int(n_today * self.args.memory_share)]
        out = self.night / "memories.jsonl"
        out.write_text("".join(json.dumps(r) + "\n" for r in keep))
        self.receipt["memories"] = {"rows": len(keep), "from_days": list(range(1, self.args.day))}
        return out

    def train_set(self, log):
        raw, clean = self.night / "train-raw.jsonl", self.night / "train.jsonl"
        sources = [str(self.night / "rewrites" / "rewrites.jsonl")]
        check = self.night / "self-check-report.json"
        if self.args.self_check and check.exists():
            # facts the current adapter got wrong: the self-check rows replace their answer rows
            wrong = {i.split("-", 1)[1] for i in json.loads(check.read_text())["replaced_items"]}
            kept = [line for line in Path(sources[0]).read_text().splitlines() if line.strip() and json.loads(line)["item_id"] not in wrong]
            (self.night / "rewrites-without-replaced.jsonl").write_text("".join(line + "\n" for line in kept))
            sources = [str(self.night / "rewrites-without-replaced.jsonl")]
            self.receipt["self_check_replaced_items"] = sorted(wrong)
        mem = self.memories()
        if mem:
            sources.append(str(mem))
        extra_rows = []
        if self.args.contrast:
            run([sys.executable, "night/contrast_rows.py", "--consolidated", str(self.night / "consolidated.json"),
                 "--perturbed", str(self.args.perturbed), "--reversed", str(self.args.reversed),
                 "--notes-context", str(self.args.notes_context), *(["--notes-form-only"] if self.args.notes_form_only else []),
                 "--out", str(self.night / "contrast.jsonl")], log)
            extra_rows.append(str(self.night / "contrast.jsonl"))
        if self.args.unknown_keys > 0:
            run([sys.executable, "night/unknown_key_rows.py", "--consolidated", str(self.night / "consolidated.json"),
                 "--day-text", str(self.run_dir / "daylog.jsonl"), str(self.run_dir / "notes.json"),
                 "--rows", str(self.args.unknown_keys), "--out", str(self.night / "unknown-keys.jsonl"),
                 *(["--own-note-in-block"] if self.args.own_note_in_block else []),
                 *(["--ask-back"] if self.args.unknown_keys_ask_back else []),
                 *(["--ask-back-scope", self.args.unknown_keys_ask_back_scope] if self.args.unknown_keys_ask_back else [])], log)
            extra_rows.append(str(self.night / "unknown-keys.jsonl"))
        if self.args.self_check and self.args.day >= 2:
            extra_rows.append(str(self.night / "self-check.jsonl"))
        if self.args.agent_anchors:
            extra_rows.append(str(self.night / "agent-anchors.jsonl"))
        if self.args.idk_rows:
            extra_rows.append(self.args.idk_rows)
        if self.args.lookup_rows:
            extra_rows.append(self.args.lookup_rows)
        public = self.args.public_anchor_rows or (str(self.night / "public-anchors.jsonl") if self.args.public_anchors else None)
        if public:
            extra_rows.append(public)
            pool = json.loads((ROOT / "eval" / "data" / "public_anchor_pool_v1.manifest.json").read_text())
            report = Path(public).with_name("public-anchors-report.json")
            self.receipt["public_anchors"] = {"rows": sum(1 for _ in Path(public).read_text().splitlines()), "file": public,
                                              "pool": pool["file"], "pool_sha256": pool["sha256"],
                                              **({k: json.loads(report.read_text()).get(k) for k in ("search_offered", "asked", "by_outcome",
                                                                                                    "kept_by_part")} if report.exists() else {})}
        soft_rows = []  # rows that never count toward the replay anchors (build_train --soft-extra)
        if self.args.kl_anchors:  # soft-anchor pools: trained with the KL term, never as hard labels (train_adapter)
            soft_rows.append(str(self.night / "kl-pools.jsonl"))
            kp = json.loads((self.night / "kl-pools-report.json").read_text())
            self.receipt["kl_pools"] = {k: kp.get(k) for k in ("candidates_sha256", "fixed", "swapped", "rows")}
        if self.args.cd_rows:  # context distillation (night/context_distill.py): soft rows read by the teacher with the notes
            soft_rows.append(self.args.cd_rows)
            rep_path = Path(self.args.cd_rows).with_name("cd-report.json")
            self.receipt["cd_rows"] = {"file": self.args.cd_rows, "rows": sum(1 for _ in Path(self.args.cd_rows).read_text().splitlines()),
                                       "weight": self.args.cd_weight, "sha256": hashlib.sha256(Path(self.args.cd_rows).read_bytes()).hexdigest(),
                                       **({k: json.loads(rep_path.read_text()).get(k) for k in ("student", "by_form")} if rep_path.exists() else {})}
        extra = (["--extra", *extra_rows] if extra_rows else []) + (["--soft-extra", *soft_rows] if soft_rows else [])
        if self.args.exclude_forms:
            extra += ["--exclude-forms", *self.args.exclude_forms]
        if self.args.drop_text_acks:
            extra += ["--drop-text-acks"]
        if self.args.repeat_forms:
            extra += ["--repeat-forms", *self.args.repeat_forms]
        if self.args.protective_share_day:  # night-9 candidate (lead 30 Oct 22:17): protective rows at that day's shares
            ref = self.runs / self.args.world / str(self.args.protective_share_day) / "night" / "train.jsonl"
            if not ref.exists():
                raise SystemExit(f"--protective-share-day {self.args.protective_share_day}: no {ref}")
            extra += ["--protective-share-from", str(ref)]
        if self.args.abstain_share > 0:
            extra += ["--abstain-anchors", str(self.abstain_anchors_path()),
                      *([str(self.abstain_anchors_plain_path())] if self.args.abstain_plain else []),
                      "--abstain-share", str(self.args.abstain_share)]
        run([sys.executable, "night/build_train.py", "--rewrites", *sources, "--anchors", str(self.anchors_path()), *extra,
             "--rows-per-fact", str(self.args.rows_per_fact), "--replay-share", str(self.args.replay_share), "--out", str(raw)], log)
        if soft_rows:  # the replay anchors stay the count without the soft rows; both counts in the receipt
            ts = json.loads(raw.with_suffix(".summary.json").read_text())
            self.receipt["replay_anchors"] = {k: ts.get(k) for k in ("soft_extra_rows", "replay_anchors_pinned", "replay_anchors_if_soft_counted")}
        if self.args.day >= SUPERSEDED_VALUE_FILTER_FROM_DAY:
            unfiltered = self.night / "train-raw-unfiltered.jsonl"
            raw.replace(unfiltered)
            run([sys.executable, "night/superseded_value_filter.py", "--notes", str(self.run_dir / "notes.json"),
                 "--consolidated", str(self.night / "consolidated.json"), "--train", str(unfiltered), "--out", str(raw),
                 "--report", str(self.night / "superseded-values.json")], log)
            sv = json.loads((self.night / "superseded-values.json").read_text())
            self.receipt["superseded_value_filter"] = {k: sv[k] for k in ("superseded_notes", "rows_in", "rows_dropped", "dropped_per_source")}
        never_told = ["--never-told"] if self.args.leak_never_told or self.args.unknown_keys > 0 else []
        leak = subprocess.run([sys.executable, "night/leak_check.py", "--world", self.args.world, "--train", str(raw),
                               "--out", str(self.night / "leak-raw.json"), "--write-clean", str(clean), *never_told], cwd=ROOT)
        run([sys.executable, "night/leak_check.py", "--world", self.args.world, "--train", str(clean),
             "--out", str(self.night / "leak.json"), *never_told], log)
        raw_report = json.loads((self.night / "leak-raw.json").read_text())
        self.receipt["leak_check"] = {"raw_hits": raw_report["hits"], "dropped_rows": raw_report.get("dropped_rows", 0),
                                      "raw_exit": leak.returncode, "clean_passed": True}
        self.receipt["train_set"] = {"rows": sum(1 for _ in clean.read_text().splitlines()), "sha256": sha256(clean)}

    def adapter_dir(self):
        return "final" if self.args.max_steps else f"epoch{self.args.epochs}"

    def train(self, log):
        a = self.args
        rsync(f"{ROOT}/", f"{HOST['name']}:{HOST['repo']}/", log, ("--exclude", ".deps", "--exclude", ".git", "--exclude", "runs", "--exclude", "__pycache__"))
        run(["ssh", HOST["name"], f"mkdir -p {self.remote}"], log)
        rsync(str(self.night / "train.jsonl"), f"{HOST['name']}:{self.remote}/train.jsonl", log)
        r = self.remote
        kl = (f" --kl-weight {a.kl_anchors} --kl-cache-dir {HOST['work']}/kl-teacher-cache" if a.kl_anchors or a.cd_rows else "") + \
             (f" --cd-weight {a.cd_weight}" if a.cd_rows else "")
        cmd = (f"set -e; {HOST['python']} night/train_adapter.py --model-dir {HOST['model_dir']} --train {r}/train.jsonl "
               f"--out-dir {r}/adapter --rank {a.rank} --targets attn_mlp --epochs {a.epochs} --lr {a.lr} --save-epochs {a.save_epochs or a.epochs}"
               f"{f' --max-steps {a.max_steps}' if a.max_steps else ''}{' --train-header' if a.train_header else ''}"
               f"{f' --seed {a.train_seed}' if a.train_seed is not None else ''}"
               f"{kl}; "
               f"LLAMA_CPP_DIR={HOST['llama_dir']} night/convert_adapter.sh {r}/adapter/{self.adapter_dir()} {r}/adapter.gguf {HOST['model_dir']} {HOST['python']}; "
               f"PYTHONPATH={HOST['llama_dir']}/gguf-py {HOST['python']} night/verify_gguf_lora.py --adapter {r}/adapter/{self.adapter_dir()} "
               f"--gguf {r}/adapter.gguf --base-config {HOST['model_dir']}/config.json --out {r}/gguf-verify.json")
        for e in self.extra_epochs():  # the epoch screen's adapters, in the same booking; a failed one never stops the night
            cmd += (f"; ( LLAMA_CPP_DIR={HOST['llama_dir']} night/convert_adapter.sh {r}/adapter/epoch{e} {r}/adapter-epoch{e}.gguf "
                    f"{HOST['model_dir']} {HOST['python']} ) || echo 'epoch {e} convert failed'")
        fleet_gpu(f"afterglow night {a.world} day {a.day}: train + convert", "90m" if a.cd_rows else "75m" if a.kl_anchors else GPU_LIMIT["train"], cmd, log)
        rsync(f"{HOST['name']}:{r}/adapter/train-report.json", str(self.night / "train-report.json"), log)
        rsync(f"{HOST['name']}:{r}/gguf-verify.json", str(self.night / "gguf-verify.json"), log)
        rsync(f"{HOST['name']}:{r}/adapter.gguf", str(self.night / "adapter.gguf"), log)

        rsync(f"{HOST['name']}:{r}/adapter/{self.adapter_dir()}/", str(self.night / "adapter") + "/", log)

    def epoch_screen_safe(self, log):
        """The epoch screen never stops a night: a failure is logged in the receipt and the night goes on."""
        try:
            self.epoch_screen(log)
        except Exception as err:  # noqa: BLE001
            self.receipt.setdefault("epoch_screen", {})["failed"] = f"{type(err).__name__}: {err}"[:300]
            (self.night / "eval").mkdir(exist_ok=True)
            (self.night / "eval" / "epoch-screen.json").write_text(json.dumps({"failed": str(err)[:300]}) + "\n")

    def extra_epochs(self):
        return sorted(int(x) for x in (self.args.save_epochs or "").split(",") if x and int(x) != self.args.epochs)

    def epoch_screen(self, log):
        """Report only (lead 30 Oct 06:54, option B): base and every saved epoch on one server, scored on facts notes-off
        and the known slices v1 + v2 in the day agent's form (eval/epoch_screen.py). Epoch 4 stays of record."""
        a, r = self.args, self.remote
        epochs = self.extra_epochs() + [a.epochs]
        extra = [x.split("=", 1) for x in (a.screen_extra or "").split(",") if x]  # other nights' adapters, same server run
        ggufs = [f"{r}/adapter-epoch{e}.gguf" for e in epochs[:-1]] + [f"{r}/adapter.gguf"] + [g for _, g in extra]
        port = 18656
        server = ["fleet", "run", "--who", "meta-night", "--goal", f"afterglow night {a.world} day {a.day}: epoch screen server",
                  "--need", "cuda,gpu-4090", "--host", HOST["name"], "--mem", HOST["mem"],
                  *(["--threads", os.environ["AFTERGLOW_HOST_THREADS"]] if os.environ.get("AFTERGLOW_HOST_THREADS") else []),
                  "--cwd", HOST["repo"], "--", "timeout", "--kill-after=20s", GPU_LIMIT["eval"], "bash", "-c",
                  (f"export LLAMA_ARG_THREADS={os.environ.get('AFTERGLOW_HOST_THREADS', '8')}; "
                   f"{HOST['llama_dir']}/build/bin/llama-server -m {HOST['target_gguf']} "
                   + " ".join(f"--lora {g}" for g in ggufs) +
                   f" --lora-init-without-apply --alias muse-glimmer-30b -ngl 99 -fa on -np 4 -c 32768 --jinja --cache-ram 0 "
                   f"--host {HOST['ip']} --port {port}")]
        out = self.night / "eval" / "epoch-screen.json"
        with open(self.logs / "epoch_screen-server.log", "w") as slog:
            proc = subprocess.Popen(server, stdout=slog, stderr=subprocess.STDOUT)
        url = f"http://{HOST['ip']}:{port}"
        try:
            for _ in range(240):
                try:
                    urllib.request.urlopen(url + "/health", timeout=5)
                    break
                except Exception:  # noqa: BLE001
                    if proc.poll() is not None:
                        raise RuntimeError("epoch screen server exited before it was up")
                    time.sleep(3)
            arms = ",".join(["base=-1"] + [f"epoch{e}={i}" for i, e in enumerate(epochs)]
                            + [f"{n}={len(epochs) + i}" for i, (n, _) in enumerate(extra)])
            # the base's and the epoch of record's facts come from the eval's arms A and B (same probes, prompt and decoding);
            # the base's known completions are cached per world (lead 30 Oct 10:11: ~12 min a night, same measure)
            ev = self.night / "eval"
            reuse = ",".join(f"{arm}={ev / f}" for arm, f in (("base", "score-night-A.json"), (f"epoch{a.epochs}", "score-night-B.json"))
                             if (ev / f).exists())
            run([sys.executable, "eval/epoch_screen.py", "--server", url, "--world", a.world, "--day", str(a.day), "--arms", arms,
                 *(["--reuse-facts", reuse] if reuse else []), "--base-cache", str(self.night.parent.parent / "_screen_cache"),
                 "--out", str(out)], log)
        finally:
            subprocess.run(["ssh", HOST["name"], f"pkill -f 'llama-server.*--port {port}'"], check=False)
            proc.wait(timeout=120)
        doc = json.loads(out.read_text())
        self.receipt["epoch_screen"] = {**self.receipt.get("epoch_screen", {}), "epochs": epochs, "file": str(out), "how": doc.get("how"),
                                        "arms": {k: {kk: v[kk] for kk in ("facts_notes_off", "known", "known_vs_base") if kk in v}
                                                 for k, v in doc["arms"].items()}}

    def eval(self, log):
        a, r = self.args, self.remote
        run(["ssh", HOST["name"], f"mkdir -p {r}/eval"], log)
        notes_remote = f"{r}/notes.json"
        rsync(str(self.run_dir / "notes.json"), f"{HOST['name']}:{notes_remote}", log)
        rsync(str(self.night / "consolidated.json"), f"{HOST['name']}:{r}/consolidated.json", log)
        rsync(str(self.anchors_path()), f"{HOST['name']}:{r}/anchors.jsonl", log)
        channel = (f"--world {a.world} --day {a.day} --lora 0:1.0 --consolidated {r}/consolidated.json "
                   f"--anchors {r}/anchors.jsonl --parallel 4 --label night --out {r}/eval/channel-night.json")
        tools = (f"--world {a.world} --day {a.day} --lora 0:1.0 --base-lora 0:0.0 --consolidated {r}/consolidated.json "
                 f"--parallel 4 --label night --out {r}/eval/tools-night.json")
        cmd = (f"CHANNEL_CHECK_ARGS={shlex.quote(channel)} TOOL_CHECK_ARGS={shlex.quote(tools)} PY={HOST['python']} MODEL_DIR={HOST['model_dir']} night/llamacpp/serve_and_eval.sh "
               f"{HOST['llama_dir']}/build/bin/llama-server {HOST['target_gguf']} {r}/adapter.gguf night {r}/eval -- "
               f"--world {a.world} --day {a.day} --notes {notes_remote} --forget-slice --gate-slice --never-told --arms A,B,C,D"
               f"{' --forget-slice-v2 --forget-arms A,B' if a.day >= FORGET_V2_FROM_DAY else ''}")
        fleet_gpu(f"afterglow night {a.world} day {a.day}: 4-arm eval in llama.cpp", GPU_LIMIT["eval"], cmd, log)
        rsync(f"{HOST['name']}:{r}/eval/", str(self.night / "eval") + "/", log)

    def eval_control_vector(self, log):
        """The same server with the vector loaded: adapter off and on on the gate slice, B on forgetting and never-told.
        Its own step, so a finished night can add it later (--control-vector ... --force report)."""
        a, r = self.args, self.remote
        path, scale = a.control_vector.rsplit(":", 1)
        extra = f"--control-vector-scaled {path} {scale}" + (f" --control-vector-layer-range {a.control_vector_layers}" if a.control_vector_layers else "")
        cmd = (f"mkdir -p {r}/eval-cv && SERVER_EXTRA={shlex.quote(extra)} PY={HOST['python']} MODEL_DIR={HOST['model_dir']} "
               f"night/llamacpp/serve_and_eval.sh {HOST['llama_dir']}/build/bin/llama-server {HOST['target_gguf']} {r}/adapter.gguf "
               f"night-cv {r}/eval-cv -- --world {a.world} --day {a.day} --forget-slice --gate-slice --never-told --arms B")
        rsync(f"{ROOT}/", f"{HOST['name']}:{HOST['repo']}/", log, ("--exclude", ".deps", "--exclude", ".git", "--exclude", "runs", "--exclude", "__pycache__"))
        fleet_gpu(f"afterglow night {a.world} day {a.day}: control-vector pass", GPU_LIMIT["eval"], cmd, log)
        run(["rsync", "-a", f"{HOST['name']}:{r}/eval-cv/", str(self.night / "eval-cv") + "/"], log)
        self.receipt["control_vector_pass"] = {"vector": a.control_vector, "layers": a.control_vector_layers, "host": HOST["name"]}

    def report(self, log):
        a = self.args
        ev = self.night / "eval"
        gguf_sha = sha256(self.night / "adapter.gguf")
        run([sys.executable, "eval/build_eval_json.py", "--world", a.world, "--day", str(a.day),
             "--scores", *[f"{arm}={ev}/score-night-{arm}.json" for arm in "ABCD"],
             "--adapter-path", str(self.night / "adapter.gguf"), "--adapter-sha256", gguf_sha, "--out", str(self.night / "eval.json"),
             *(["--gate", str(ev / "gate-night.json")] if (ev / "gate-night.json").exists() else [])], log)
        e = json.loads((self.night / "eval.json").read_text())
        channel = ev / "channel-night.json"
        if channel.exists():
            e["content_channel"] = {k: v for k, v in json.loads(channel.read_text()).items() if k != "replies"}
        product = ev / "product-abstention-night.json"  # option B's measurement, when it exists (report, not a gate under v3)
        if product.exists():
            e["abstention_product_form"] = json.loads(product.read_text())
        tool_check = ev / "tools-night.json"
        if tool_check.exists():
            e["tool_calls"] = {k: v for k, v in json.loads(tool_check.read_text()).items() if k != "turns"}
        behaviour = self.night / "behaviour-check.json"
        if behaviour.exists():  # Meta steering's behaviour check of this adapter (report only, not a gate)
            doc = json.loads(behaviour.read_text())
            e["behaviour_check"] = doc.get("behaviour_check", doc)
        if channel.exists() or behaviour.exists() or tool_check.exists() or product.exists():
            (self.night / "eval.json").write_text(json.dumps(e, indent=1) + "\n")
        cv_path, main_gate = self.night / "eval-cv" / "gate-night-cv.json", ev / "gate-night.json"
        if a.control_vector and cv_path.exists() and main_gate.exists():
            import simpleqa_gate
            cv, base = json.loads(cv_path.read_text()), json.loads(main_gate.read_text())
            rec = simpleqa_gate.gate_record({"A": base["answers"]["A"], "B": cv["answers"]["B"]}, "night-cv-vs-base")
            rec.update({"vector": a.control_vector, "layers": a.control_vector_layers,
                        "base_with_vector": cv["arms"]["A"]["abstain_rate"]})
            (self.night / "eval-cv" / "gate-night-cv-vs-base.json").write_text(json.dumps(rec, indent=1) + "\n")
            e["abstention_gate_with_control_vector"] = {k: v for k, v in rec.items() if k != "answers"}
            cv_b = self.night / "eval-cv" / "score-night-cv-B.json"
            if cv_b.exists():  # report only: the B arm with the vector on the forgetting slice and never-told
                by_kind = {a: json.loads((ev / f"score-night-{a}.json").read_text())["summary"]["by_kind"] for a in "AB"}
                by_kind["B_with_vector"] = json.loads(cv_b.read_text())["summary"]["by_kind"]
                e["control_vector_pass"] = {arm: {k: {"pass": v["pass"], "n": v["n"]} for k, v in kinds.items()
                                                  if k in ("forget_mmlu", "forget_tool", "never_told", "general")}
                                            for arm, kinds in by_kind.items()}
            (self.night / "eval.json").write_text(json.dumps(e, indent=1) + "\n")
        verify = json.loads((self.night / "gguf-verify.json").read_text())
        decision = decide(e, verify, self.receipt.get("leak_check", {}).get("clean_passed", False), a.gate_with_control_vector)
        keep = decision["decision"] == "keep"
        (self.night / "decision.json").write_text(json.dumps(decision, indent=1) + "\n")
        if a.night_dir == "night":  # an experiment arm never becomes the world's kept adapter
            pointer_path = self.runs / a.world / "adapters.json"
            pointer = json.loads(pointer_path.read_text()) if pointer_path.exists() else {"current": None, "history": []}
            entry = {"day": a.day, "gguf": str(self.night / "adapter.gguf"), "gguf_sha256": gguf_sha,
                     "host_gguf": f"{HOST['name']}:{self.remote}/adapter.gguf", "decision": decision["decision"]}
            pointer["history"].append(entry)
            if keep:
                pointer["current"] = entry
            pointer_path.write_text(json.dumps(pointer, indent=1) + "\n")
        self.receipt["hashes"] = {
            "daylog": sha256(self.run_dir / "daylog.jsonl"), "notes": sha256(self.run_dir / "notes.json"),
            "consolidated": sha256(self.night / "consolidated.json"), "rewrites": sha256(self.night / "rewrites" / "rewrites.jsonl"),
            "anchors": sha256(self.anchors_path()), "train": sha256(self.night / "train.jsonl"),
            "adapter_safetensors": sha256(self.night / "adapter" / "adapter_model.safetensors"), "adapter_gguf": gguf_sha,
            "heldout_frozen": json.loads((ROOT / "worlds" / a.world / "heldout" / "FROZEN.json").read_text())["sha256"],
            "forget_slice": sha256(ROOT / "eval" / "data" / "forget_slice_v1.jsonl"),
            "forget_slice_v2": sha256(ROOT / "eval" / "data" / "forget_slice_v2.jsonl") if a.day >= FORGET_V2_FROM_DAY else None,
            "gate_slice": sha256(ROOT / "eval" / "data" / "simpleqa" / "gate_slice_v1.jsonl"),
            "never_told": json.loads((ROOT / "eval" / "data" / "never_told" / "FROZEN.json").read_text())["files"].get(f"{a.world}.jsonl"),
            "eval": sha256(self.night / "eval.json"), "decision": sha256(self.night / "decision.json")}
        self.receipt["train_report"] = {k: v for k, v in json.loads((self.night / "train-report.json").read_text()).items() if k != "curve"}
        self.receipt["decision"] = decision["decision"]
        self.receipt["ended"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")

    def public_anchors(self, log):
        """Public anchor rows (night/public_anchor_rows.py): base answers or looks up public questions in the served
        agent form, set against the ask-back rows (w4 night 5c, lead 30 Oct 05:25: the night-6 recipe draft)."""
        server = os.environ.get("GLIMMER_BASE_URL")
        run([sys.executable, "night/public_anchor_rows.py", "--world", self.args.world, "--rows", str(self.args.public_anchors),
             *(["--server", server] if server else []), "--out", str(self.night / "public-anchors.jsonl"),
             "--report", str(self.night / "public-anchors-report.json")], log)

    def kl_pools(self, log):
        """Soft-anchor pool rows (night/kl_pool_rows.py, research round 2 #1): base replies in the served agent form to
        the frozen pools (obscure, known, people; made once per world, cached in runs/<w>/_kl_pools) and to this night's
        user-fact questions with the user named as a public person. Needs a base server (GLIMMER_BASE_URL)."""
        server = os.environ.get("GLIMMER_BASE_URL")
        if not server:
            raise RuntimeError("the soft-anchor pools need a base server: set GLIMMER_BASE_URL (phase P)")
        run([sys.executable, "night/kl_pool_rows.py", "--world", self.args.world, "--server", server,
             "--rewrites", str(self.night / "rewrites" / "rewrites.jsonl"), "--cache-dir", str(self.runs / self.args.world / "_kl_pools"),
             "--out", str(self.night / "kl-pools.jsonl"), "--report", str(self.night / "kl-pools-report.json")], log)

    def go(self):
        n = self.night
        steps = [("consolidate", n / "consolidated.json", self.consolidate),
                 ("rewrites", n / "rewrites" / "rewrites.jsonl", self.rewrites),
                 *([("self_check", n / "self-check.jsonl", self.self_check)] if self.args.self_check and self.args.day >= 2 else []),
                 *([("agent_anchors", n / "agent-anchors.jsonl", self.agent_anchors)] if self.args.agent_anchors else []),
                 *([("public_anchors", n / "public-anchors.jsonl", self.public_anchors)] if self.args.public_anchors else []),
                 *([("kl_pools", n / "kl-pools.jsonl", self.kl_pools)] if self.args.kl_anchors else []),
                 ("anchors", self.anchors_path(), self.anchors),
                 *([("abstain_anchors", self.abstain_anchors_path(), self.abstain_anchors)] if self.args.abstain_share > 0 else []),
                 *([("abstain_anchors_plain", self.abstain_anchors_plain_path(), lambda log: self.abstain_anchors(log, plain=True))]
                   if self.args.abstain_share > 0 and self.args.abstain_plain else []),
                 ("train_set", n / "train.jsonl", self.train_set),
                 ("train", n / "adapter.gguf", self.train),
                 ("eval", n / "eval" / "score-night-D.json", self.eval),
                 *([("epoch_screen", n / "eval" / "epoch-screen.json", self.epoch_screen_safe)] if self.args.save_epochs else []),
                 *([("eval_control_vector", n / "eval-cv" / "gate-night-cv.json", self.eval_control_vector)] if self.args.control_vector else []),
                 ("report", n / "decision.json", self.report)]
        for name, output, fn in steps:
            self.step(name, output, fn)
            if name == self.args.stop_after:
                self.save()
                print(json.dumps({"night": str(n), "stopped_after": name}))
                return
        self.save()
        print(json.dumps({"night": str(n), "decision": json.loads((n / "decision.json").read_text())}, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", required=True)
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--teacher", default="glimmer", choices=["glimmer", "spark-sub"])
    ap.add_argument("--runs", help="runs root (default: AFTERGLOW_RUNS or the main checkout's runs/)")
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--save-epochs", help="comma list of epochs to keep (e.g. 2,3,4): the extra ones are converted and scored by "
                    "the epoch screen (eval/epoch_screen.py; report only, lead 30 Oct 06:54); default: the last epoch only")
    ap.add_argument("--screen-extra", help="name=remote GGUF,...: other adapters scored in the same epoch-screen server run "
                    "(lead 30 Oct 11:16: the W=1 screen scores w3 night 6's epochs beside its own)")
    ap.add_argument("--cd-rows", help="context-distillation rows (night/context_distill.py cd-rows.jsonl), trained as soft rows "
                    "toward plain Glimmer reading the user's notes (lead 30 Oct 12:23: the cd screen on w3 day 6)")
    ap.add_argument("--cd-weight", type=float, default=1.0, help="the KL weight of the --cd-rows rows")
    ap.add_argument("--kl-anchors", type=float, default=0.0, help="soft anchors (research round 2 #1; lead 30 Oct 08:03): "
                    "the KL weight W for plain Glimmer's own rows (anchors, agent and public anchors, the kl pools made by the "
                    "kl_pools step); fact and ask-back rows keep cross-entropy. 0 = off")
    ap.add_argument("--train-seed", type=int, help="train_adapter.py --seed (default: its own, 20261027); a second seed on the same "
                    "train set measures the run-to-run spread (w4 night 5c2, lead 30 Oct 06:46)")
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--rows-per-fact", type=int, default=12)
    ap.add_argument("--replay-share", type=float, default=0.10)
    ap.add_argument("--abstain-share", type=float, default=0.0, help="abstaining anchors (base answers on SimpleQA pool) share")
    ap.add_argument("--abstain-plain", action="store_true", help="split the abstaining anchors half and half with bare-question answers")
    ap.add_argument("--memory-share", type=float, default=0.3, help="earlier days' rewrites, as a share of today's rows")
    ap.add_argument("--force", nargs="*", default=[], help="steps to redo even if their output exists")
    ap.add_argument("--stop-after", help="stop after this step (e.g. train_set: prepare data while the GPU is busy)")
    ap.add_argument("--night-dir", default="night", help="output folder under runs/<w>/<d>/ (an experiment arm; it never moves the kept adapter)")
    ap.add_argument("--rules-as-facts", action="store_true", help="also learn each named preference/procedure as a fact")
    ap.add_argument("--contrast", action="store_true", help="add key-anchored and look-alike contrast rows (night/contrast_rows.py)")
    ap.add_argument("--perturbed", type=int, default=0, help="with --contrast: entity-perturbed 'not in your notes' rows per item")
    ap.add_argument("--max-steps", type=int, default=0, help="stop training at this optimizer step (matched-steps controls)")
    ap.add_argument("--reversed", type=int, default=0, help="with --contrast: quoted key-as-target rows per item")
    ap.add_argument("--notes-context", type=int, default=0, help="with --contrast: contrast rows with the notes in the prompt")
    ap.add_argument("--notes-form-only", action="store_true", help="with --contrast: contrast only in the notes-in-prompt form")
    ap.add_argument("--exclude-forms", nargs="*", default=[], help="rewrite forms never picked for facts (e.g. qa_reverse)")
    ap.add_argument("--unknown-keys", type=int, default=0, help="unknown-key rows (night/unknown_key_rows.py), each with a known pair")
    ap.add_argument("--unknown-keys-ask-back", action="store_true",
                    help="unknown-key replies name the asked key and ask for it, with no notes wording (night-5 candidate)")
    ap.add_argument("--unknown-keys-ask-back-scope", choices=("all", "own"), default="all",
                    help="own: ask back only on the user's own keys asked bare (w4 night 5b, lead 30 Oct 01:38)")
    ap.add_argument("--self-check", action="store_true", help="from night 2: ask the current adapter each earlier fact "
                    "on the day server; wrong -> 'let me check your notes' rows replace its answer rows (night/self_check_rows.py)")
    ap.add_argument("--control-vector", help="PATH:SCALE of a control-vector GGUF on the training host: the eval also runs "
                    "the gate slice, forgetting and never-told with it loaded, and decision.json reports that gate")
    ap.add_argument("--control-vector-layers", help="'FIRST LAST' for --control-vector-layer-range")
    ap.add_argument("--gate-with-control-vector", action="store_true", help="the gate with the vector loaded is the gate of record")
    ap.add_argument("--agent-anchors", type=int, default=0, help="base replies to day-agent turns, kept when they take the "
                    "intended action (night/agent_anchor_rows.py; the day server, slot 1)")
    ap.add_argument("--drop-text-acks", action="store_true", help="drop store requests answered in text (night/build_train.py)")
    ap.add_argument("--idk-rows", help="graded IDK rows from night/idk_rows.py rows (base wrong -> I don't know, right -> its answer)")
    ap.add_argument("--public-anchors", type=int, default=0, help="N public anchor rows made by the night (step public_anchors; "
                    "base server GLIMMER_BASE_URL, else the serving box), search_web offered (night-6 recipe draft)")
    ap.add_argument("--public-anchor-rows", help="public anchor rows from night/public_anchor_rows.py (base answers or searches "
                    "public questions in the served agent form; set against the ask-back rows)")
    ap.add_argument("--lookup-rows", help="look-it-up rows from night/lookup_rows.py rows (misses -> recall call or 'not sure', hits -> answer)")
    ap.add_argument("--own-note-in-block", action="store_true", help="unknown-key look-alike rows in the notes form hold the "
                    "user's own note for the key (night/unknown_key_rows.py)")
    ap.add_argument("--repeat-forms", nargs="*", default=[], help="FORM:N row weighting in build_train (e.g. agent_remember:3 agent_recall:3)")
    ap.add_argument("--protective-share-day", type=int, help="keep ask-back, known-pair and agent rows at their share in that day's "
                    "night of record (runs/<w>/<D>/night/train.jsonl) by repeating them; public and fact rows are not scaled")
    ap.add_argument("--train-header", action="store_true", help="train the answer header's recipient too (user channel on the chat endpoint)")
    ap.add_argument("--leak-never-told", action="store_true", help="leak-check the never-told probes too (on with --unknown-keys)")
    ap.add_argument("--seed-rewrites-from", help="reuse unchanged items' rewrites from this night folder (e.g. night)")
    args = ap.parse_args()
    Night(args).go()


if __name__ == "__main__":
    main()
