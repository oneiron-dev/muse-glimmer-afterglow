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
  train        QLoRA adapter on the host, converted to GGUF and checked module by module
  eval         llama-server with the adapter: arms A/B/C/D on the held-out probes and the forgetting slice,
               and the SimpleQA abstention gate slice with the adapter off and on
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
    },
}
HOST = {**HOSTS[os.environ.get("AFTERGLOW_TRAIN_HOST", "mel")],
        "mem": os.environ.get("AFTERGLOW_HOST_MEM", "22G")}  # fleet RAM booking for each GPU step
GPU_LIMIT = {"anchors": "15m", "train": "45m", "eval": "40m"}
LEARNED_MIN_LOWER = 0.0  # keep only if the B-A per-fact interval is above zero


ABSTENTION_RULES = ("v3", "product", "product-2s", "report-only")  # v3 = rule of record until the owner rules (META-ABSTENTION-GATE)
PRODUCT_2S_BAND, PRODUCT_NT_DROP = 0.10, 3


def product_two_sided(product):
    """Option B, two-sided: in the day agent's own form, the adapter's general abstain rate within 10 points of the
    base's (either way) and its never-told abstentions at most 3 below the base's."""
    a, b = product["arms"]["adapter"], product["arms"]["base"]
    rate = lambda x: x["general"]["abstain"] / x["general"]["n"]  # noqa: E731
    return product.get("complete", True) and abs(rate(a) - rate(b)) <= PRODUCT_2S_BAND + 1e-9 \
        and a["never_told"]["abstain"] >= b["never_told"]["abstain"] - PRODUCT_NT_DROP


ABSTENTION_RULE_OF_RECORD = "product-2s"  # owner ruling 29 Oct 11:2x (META-ABSTENTION-GATE = B); v3 stays a disclosed check


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
    pending = False
    if abstention_rule in ("product", "product-2s"):  # option B: abstention judged in the day agent's own form
        if product:
            checks["abstention_product_form"] = product["passed"] if abstention_rule == "product" else product_two_sided(product)
        else:  # measured after the night on the day server; until then the night is pending, never kept
            pending = True
    elif abstention_rule == "report-only":  # option C: abstention reported, never a gate
        pass
    elif gate_with_control_vector and cv_gate:
        checks["abstention_gate_with_control_vector"] = cv_gate["passed"]
    elif gate:
        checks["abstention_gate"] = gate["passed"]
    keep = all(checks.values()) and not pending
    why = ("all gates passed" if keep else "failed: " + ", ".join(k for k, v in checks.items() if not v))
    why += (f"; learned B-A {learned.get('est')} {learned.get('ci95')}, forgot {e['forgot']['delta'] if e['forgot'] else None} "
            f"{e['forgot']['ci95'] if e['forgot'] else None}")
    why += (f"; abstain rate {gate['arms']['A']['abstain_rate']} -> {gate['arms']['B']['abstain_rate']}, exact right "
            f"{gate['arms']['A']['exact_correct']} -> {gate['arms']['B']['exact_correct']}" if gate
            else "; abstention gate not measured")
    nt = e["arms"].get("B", {}).get("by_kind", {}).get("never_told")
    if nt:  # report only, no gate yet
        why += f"; never-told B abstained {nt['pass']}/{nt['n']}"
    if e.get("content_channel"):
        why += f"; content channel {e['content_channel']['content_ok']}/{e['content_channel']['n']}"
    if e.get("tool_calls"):
        c = e["tool_calls"]["counts"]
        why += f"; tool calls {c['base']['right']} -> {c['adapter']['right']} of {c['adapter']['n']}"
    if pending:
        why = ("pending: abstention in the product form not measured yet (re-decide with eval/redecide_night.py --rule "
               f"{abstention_rule} --of-record); " + why)
    out = {"decision": "pending" if pending and all(checks.values()) else ("keep" if keep else "rollback"), "why": why,
           "checks": checks, "abstention_rule": abstention_rule}
    if gate and abstention_rule != "v3":  # the SimpleQA instructed gate, shown beside the decision, never a blocker
        out["disclosed_checks"] = {"abstention_gate_v3": gate["passed"]}
    if product:
        out["abstention_product_form"] = {k: v for k, v in product.items() if k != "answers"}
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
                "drop_text_acks": a.drop_text_acks, "repeat_forms": a.repeat_forms, "own_note_in_block": a.own_note_in_block,
                "idk_rows": a.idk_rows, "lookup_rows": a.lookup_rows}

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
        manifest = json.loads((self.runs / "serving" / "adapters.json").read_text())
        run([sys.executable, "night/agent_anchor_rows.py", "--world", self.args.world, "--consolidated", str(self.night / "consolidated.json"),
             "--n-loras", str(len(manifest["adapters"])), "--turns", str(self.args.agent_anchors),
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
                 *(["--own-note-in-block"] if self.args.own_note_in_block else [])], log)
            extra_rows.append(str(self.night / "unknown-keys.jsonl"))
        if self.args.self_check and self.args.day >= 2:
            extra_rows.append(str(self.night / "self-check.jsonl"))
        if self.args.agent_anchors:
            extra_rows.append(str(self.night / "agent-anchors.jsonl"))
        if self.args.idk_rows:
            extra_rows.append(self.args.idk_rows)
        if self.args.lookup_rows:
            extra_rows.append(self.args.lookup_rows)
        extra = ["--extra", *extra_rows] if extra_rows else []
        if self.args.exclude_forms:
            extra += ["--exclude-forms", *self.args.exclude_forms]
        if self.args.drop_text_acks:
            extra += ["--drop-text-acks"]
        if self.args.repeat_forms:
            extra += ["--repeat-forms", *self.args.repeat_forms]
        if self.args.abstain_share > 0:
            extra += ["--abstain-anchors", str(self.abstain_anchors_path()),
                      *([str(self.abstain_anchors_plain_path())] if self.args.abstain_plain else []),
                      "--abstain-share", str(self.args.abstain_share)]
        run([sys.executable, "night/build_train.py", "--rewrites", *sources, "--anchors", str(self.anchors_path()), *extra,
             "--rows-per-fact", str(self.args.rows_per_fact), "--replay-share", str(self.args.replay_share), "--out", str(raw)], log)
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
        cmd = (f"set -e; {HOST['python']} night/train_adapter.py --model-dir {HOST['model_dir']} --train {r}/train.jsonl "
               f"--out-dir {r}/adapter --rank {a.rank} --targets attn_mlp --epochs {a.epochs} --lr {a.lr} --save-epochs {a.epochs}"
               f"{f' --max-steps {a.max_steps}' if a.max_steps else ''}{' --train-header' if a.train_header else ''}; "
               f"LLAMA_CPP_DIR={HOST['llama_dir']} night/convert_adapter.sh {r}/adapter/{self.adapter_dir()} {r}/adapter.gguf {HOST['model_dir']} {HOST['python']}; "
               f"PYTHONPATH={HOST['llama_dir']}/gguf-py {HOST['python']} night/verify_gguf_lora.py --adapter {r}/adapter/{self.adapter_dir()} "
               f"--gguf {r}/adapter.gguf --base-config {HOST['model_dir']}/config.json --out {r}/gguf-verify.json")
        fleet_gpu(f"afterglow night {a.world} day {a.day}: train + convert", GPU_LIMIT["train"], cmd, log)
        rsync(f"{HOST['name']}:{r}/adapter/train-report.json", str(self.night / "train-report.json"), log)
        rsync(f"{HOST['name']}:{r}/gguf-verify.json", str(self.night / "gguf-verify.json"), log)
        rsync(f"{HOST['name']}:{r}/adapter.gguf", str(self.night / "adapter.gguf"), log)
        rsync(f"{HOST['name']}:{r}/adapter/{self.adapter_dir()}/", str(self.night / "adapter") + "/", log)

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
               f"--world {a.world} --day {a.day} --notes {notes_remote} --forget-slice --gate-slice --never-told --arms A,B,C,D")
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
            "gate_slice": sha256(ROOT / "eval" / "data" / "simpleqa" / "gate_slice_v1.jsonl"),
            "never_told": json.loads((ROOT / "eval" / "data" / "never_told" / "FROZEN.json").read_text())["files"].get(f"{a.world}.jsonl"),
            "eval": sha256(self.night / "eval.json"), "decision": sha256(self.night / "decision.json")}
        self.receipt["train_report"] = {k: v for k, v in json.loads((self.night / "train-report.json").read_text()).items() if k != "curve"}
        self.receipt["decision"] = decision["decision"]
        self.receipt["ended"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")

    def go(self):
        n = self.night
        steps = [("consolidate", n / "consolidated.json", self.consolidate),
                 ("rewrites", n / "rewrites" / "rewrites.jsonl", self.rewrites),
                 *([("self_check", n / "self-check.jsonl", self.self_check)] if self.args.self_check and self.args.day >= 2 else []),
                 *([("agent_anchors", n / "agent-anchors.jsonl", self.agent_anchors)] if self.args.agent_anchors else []),
                 ("anchors", self.anchors_path(), self.anchors),
                 *([("abstain_anchors", self.abstain_anchors_path(), self.abstain_anchors)] if self.args.abstain_share > 0 else []),
                 *([("abstain_anchors_plain", self.abstain_anchors_plain_path(), lambda log: self.abstain_anchors(log, plain=True))]
                   if self.args.abstain_share > 0 and self.args.abstain_plain else []),
                 ("train_set", n / "train.jsonl", self.train_set),
                 ("train", n / "adapter.gguf", self.train),
                 ("eval", n / "eval" / "score-night-D.json", self.eval),
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
    ap.add_argument("--lookup-rows", help="look-it-up rows from night/lookup_rows.py rows (misses -> recall call or 'not sure', hits -> answer)")
    ap.add_argument("--own-note-in-block", action="store_true", help="unknown-key look-alike rows in the notes form hold the "
                    "user's own note for the key (night/unknown_key_rows.py)")
    ap.add_argument("--repeat-forms", nargs="*", default=[], help="FORM:N row weighting in build_train (e.g. agent_remember:3 agent_recall:3)")
    ap.add_argument("--train-header", action="store_true", help="train the answer header's recipient too (user channel on the chat endpoint)")
    ap.add_argument("--leak-never-told", action="store_true", help="leak-check the never-told probes too (on with --unknown-keys)")
    ap.add_argument("--seed-rewrites-from", help="reuse unchanged items' rewrites from this night folder (e.g. night)")
    args = ap.parse_args()
    Night(args).go()


if __name__ == "__main__":
    main()
