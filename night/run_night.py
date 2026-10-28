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

HOST = {
    "name": "mel",
    "repo": "/data/meta-night/repo",
    "work": "/data/meta-night/nights",
    "python": "/home/lexi/c-pilot-gpu-venv-20261014/bin/python",
    "model_dir": "/home/lexi/c-measure-gpu-20261014/models",
    "base_id": "unsloth-Muse-Glimmer-30B-bnb-4bit-ecedda39",
    "target_gguf": "/data/meta-night/models/Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf",
    "llama_dir": "/data/meta-night/llama.cpp",
    "mem": os.environ.get("AFTERGLOW_HOST_MEM", "22G"),  # fleet RAM booking for each GPU step
}
GPU_LIMIT = {"anchors": "15m", "train": "45m", "eval": "40m"}
LEARNED_MIN_LOWER = 0.0  # keep only if the B-A per-fact interval is above zero


def decide(e, verify, leak_passed):
    """Keep the adapter only if every gate passes: clean data, GGUF = PEFT, learned (B-A interval above 0),
    no loss detected (forgetting interval's lower bound above -5 points), no new tool failure and, when measured,
    abstention kept (SimpleQA gate slice, two-sided: abstain rate within 10 points of the base's and exact-match right
    answers at least the base's minus 3)."""
    learned = e["learned"].get("B_minus_A", {})
    checks = {"leak_check": leak_passed,
              "gguf_matches_peft": verify.get("passed", False),
              "learned_gate": bool(learned.get("ci95")) and learned["ci95"][0] > LEARNED_MIN_LOWER,
              "forgot_gate": bool(e["forgot"]) and e["forgot"]["ci95"][0] > -0.05,
              "tool_gate": bool(e["forgot"]) and not e["forgot"]["critical_tool_failures"]}
    gate = e.get("abstention_gate")
    if gate:
        checks["abstention_gate"] = gate["passed"]
    keep = all(checks.values())
    why = ("all gates passed" if keep else "failed: " + ", ".join(k for k, v in checks.items() if not v))
    why += (f"; learned B-A {learned.get('est')} {learned.get('ci95')}, forgot {e['forgot']['delta'] if e['forgot'] else None} "
            f"{e['forgot']['ci95'] if e['forgot'] else None}")
    why += (f"; abstain rate {gate['arms']['A']['abstain_rate']} -> {gate['arms']['B']['abstain_rate']}, exact right "
            f"{gate['arms']['A']['exact_correct']} -> {gate['arms']['B']['exact_correct']}" if gate
            else "; abstention gate not measured")
    nt = e["arms"].get("B", {}).get("by_kind", {}).get("never_told")
    if nt:  # report only, no gate yet
        why += f"; never-told B abstained {nt['pass']}/{nt['n']}"
    out = {"decision": "keep" if keep else "rollback", "why": why, "checks": checks}
    if gate:
        out["abstention"] = {"base": gate["arms"]["A"]["abstain_rate"], "after": gate["arms"]["B"]["abstain_rate"],
                             "right_base": gate["arms"]["A"]["exact_correct"], "right_after": gate["arms"]["B"]["exact_correct"],
                             "n": gate["arms"]["B"]["n"], "slice": "gate_slice_v1",
                             "rule": gate.get("rule", "after >= base - 0.10")}
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
    cmd = ["fleet", "run", "--who", "meta-night", "--goal", goal, "--need", "cuda,gpu-4090", "--host", HOST["name"],
           "--mem", HOST["mem"], "--cwd", HOST["repo"], "--", "timeout", "--kill-after=20s", limit, "bash", "-c", remote_cmd]
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
                "self_check": a.self_check}

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
    def consolidate(self, log):
        run([sys.executable, "night/consolidate.py", "--world", self.args.world, "--day", str(self.args.day),
             "--teacher", self.args.teacher, "--runs", str(self.runs), "--out-dir", str(self.night)], log)

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
        run([sys.executable, "night/self_check_rows.py", "--world", self.args.world, "--day", str(self.args.day),
             "--consolidated", str(self.night / "consolidated.json"), "--runs", str(self.runs),
             "--out", str(self.night / "self-check.jsonl"), "--report", str(self.night / "self-check-report.json")], log)
        report = json.loads((self.night / "self-check-report.json").read_text())
        self.receipt["self_check"] = {k: report[k] for k in ("adapter_id", "adapter_sha256", "items", "known", "unsure", "wrong", "tokens")}

    def memories(self):
        """A share of earlier days' rewrites, for items not superseded since (multi-day replay)."""
        if self.args.day <= 1 or self.args.memory_share <= 0:
            return None
        today = json.loads((self.night / "consolidated.json").read_text())
        superseded_values = {i["corrects"].lower() for i in today["items"] if i.get("corrects")}
        check = self.night / "self-check-report.json"
        unsure = set(json.loads(check.read_text())["unsure_items"]) if self.args.self_check and check.exists() else set()
        rows = []
        for d in range(1, self.args.day):
            path = self.runs / self.args.world / str(d) / "night" / "rewrites" / "rewrites.jsonl"
            if path.exists():
                for line in path.read_text().splitlines():
                    r = json.loads(line)
                    if r["valid"] and not any(v in r["assistant"].lower() for v in superseded_values) \
                            and f"d{d}-{r['item_id']}" not in unsure:
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
                 "--rows", str(self.args.unknown_keys), "--out", str(self.night / "unknown-keys.jsonl")], log)
            extra_rows.append(str(self.night / "unknown-keys.jsonl"))
        if self.args.self_check and self.args.day >= 2:
            extra_rows.append(str(self.night / "self-check.jsonl"))
        extra = ["--extra", *extra_rows] if extra_rows else []
        if self.args.exclude_forms:
            extra += ["--exclude-forms", *self.args.exclude_forms]
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
               f"{f' --max-steps {a.max_steps}' if a.max_steps else ''}; "
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
        cmd = (f"PY={HOST['python']} MODEL_DIR={HOST['model_dir']} night/llamacpp/serve_and_eval.sh "
               f"{HOST['llama_dir']}/build/bin/llama-server {HOST['target_gguf']} {r}/adapter.gguf night {r}/eval -- "
               f"--world {a.world} --day {a.day} --notes {notes_remote} --forget-slice --gate-slice --never-told --arms A,B,C,D")
        fleet_gpu(f"afterglow night {a.world} day {a.day}: 4-arm eval in llama.cpp", GPU_LIMIT["eval"], cmd, log)
        rsync(f"{HOST['name']}:{r}/eval/", str(self.night / "eval") + "/", log)

    def report(self, log):
        a = self.args
        ev = self.night / "eval"
        gguf_sha = sha256(self.night / "adapter.gguf")
        run([sys.executable, "eval/build_eval_json.py", "--world", a.world, "--day", str(a.day),
             "--scores", *[f"{arm}={ev}/score-night-{arm}.json" for arm in "ABCD"],
             "--adapter-path", str(self.night / "adapter.gguf"), "--adapter-sha256", gguf_sha, "--out", str(self.night / "eval.json"),
             *(["--gate", str(ev / "gate-night.json")] if (ev / "gate-night.json").exists() else [])], log)
        e = json.loads((self.night / "eval.json").read_text())
        verify = json.loads((self.night / "gguf-verify.json").read_text())
        decision = decide(e, verify, self.receipt.get("leak_check", {}).get("clean_passed", False))
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
                 ("anchors", self.anchors_path(), self.anchors),
                 *([("abstain_anchors", self.abstain_anchors_path(), self.abstain_anchors)] if self.args.abstain_share > 0 else []),
                 *([("abstain_anchors_plain", self.abstain_anchors_plain_path(), lambda log: self.abstain_anchors(log, plain=True))]
                   if self.args.abstain_share > 0 and self.args.abstain_plain else []),
                 ("train_set", n / "train.jsonl", self.train_set),
                 ("train", n / "adapter.gguf", self.train),
                 ("eval", n / "eval" / "score-night-D.json", self.eval),
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
                    "on the day server; unsure -> 'let me check your notes' rows (night/self_check_rows.py)")
    ap.add_argument("--leak-never-told", action="store_true", help="leak-check the never-told probes too (on with --unknown-keys)")
    ap.add_argument("--seed-rewrites-from", help="reuse unchanged items' rewrites from this night folder (e.g. night)")
    args = ap.parse_args()
    Night(args).go()


if __name__ == "__main__":
    main()
