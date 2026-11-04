#!/usr/bin/env python3
"""Land one Fireworks SFT job (run on the MacBook): wait for it to complete, download the adapter (signed-in firectl;
API keys cannot download), copy it to nelly and mel with sha256 checked on both, delete the Mac copy (logged), convert +
gguf-verify + apply-check on nelly CPU (night/fireworks_convert_host.sh), copy the GGUF to mel, and write a receipt that
lists every way the recipe differs from the nights. Exits 0 when landed, 1 when the job failed or a check failed.

Usage: FIREWORKS_API_KEY=... night/fireworks_landing.py JOB_ID --dataset-dir DIR --world wN --seed s1 --out-root DIR
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://api.fireworks.ai/v1/accounts/oneiron"
STAGE = Path("/Users/olety/.claude/jobs/4f275478/tmp/stage")
DELETED_LOG = Path("/Users/olety/Desktop/temp/meta-hack-20261014/builders/pa/disk/DELETED-20261104.jsonl")
BUS = Path("/Users/olety/Desktop/temp/meta-hack-20261014/builders/pa/BUS.md")
HOSTS = ("nelly", "mel")
REMOTE_ROOT = "/data/fireworks"
DONE = ("JOB_STATE_COMPLETED",)
FAILED = ("JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED", "JOB_STATE_DELETING")
KEEP_FILES = ("adapter_config.json", ".fw_checkpoint_metadata.json")
DIFFERENCES = [
    "different recipe (bf16 Fireworks SFT, no cd rows): Fireworks managed SFT on bf16 Muse Glimmer 30B; the nights train QLoRA on the NF4 base",
    "rows: the night train set minus its context-distillation rows (form cd_*, soft KL rows with no hard-label form); every other row as a hard label, as in the nights (kl weight 0)",
    "optimizer: Fireworks' trainer (AdamW, bf16); the nights use bitsandbytes AdamW8bit, weight decay 0, grad clip 0.3",
    "modules: Fireworks also adapts the attention gate (self_attn.gate_proj -> GGUF attn_gate, applied by llama.cpp); the nights adapt q,k,v,o + MLP gate/up/down",
    "system line: Fireworks renders 'Reasoning strength: high.' (its renderer has no per-row or per-job strength); the nights render 'minimal' on plain rows and 'low' on agent/public-anchor rows; every other byte of every row renders identically (render check)",
    "loss span: Fireworks trains the assistant header ' to=user<|message|>' on plain rows; the nights train from after it (train-header off); agent/public-anchor rows train the same span in both",
    "loss normalisation: Fireworks normalises the loss within each example; the nights average over the batch's reply tokens",
    "length: the nights cut plain rows at 512 tokens; Fireworks max context 4096 (no cut)",
    "seed: Fireworks takes no training seed; s1 = the train-set row order, s2 = rows shuffled with seed 20261104",
]


def call(path):
    req = urllib.request.Request(f"{API}{path}", headers={"Authorization": f"Bearer {os.environ['FIREWORKS_API_KEY']}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=isinstance(cmd, str), check=True, capture_output=True, text=True, **kw).stdout


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def log(out, msg):
    line = f"{datetime.now().strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(out / "landing.log", "a") as f:
        f.write(line + "\n")


def wait(job_id, out, poll):
    last = None
    while True:
        try:
            d = call(f"/supervisedFineTuningJobs/{job_id}")
        except Exception as e:  # a network blip is not a job failure
            log(out, f"poll error {str(e)[:120]}")
            time.sleep(poll)
            continue
        state = d.get("state")
        msg = (d.get("status") or {}).get("message", "")
        if (state, msg) != last:
            log(out, f"{state} | {msg}")
            last = (state, msg)
        if state in DONE or state in FAILED:
            return d
        time.sleep(poll)


def fetch(url, dest):
    if url:
        with urllib.request.urlopen(url, timeout=120) as r:
            dest.write_bytes(r.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id")
    ap.add_argument("--dataset-dir", required=True)
    ap.add_argument("--world", required=True)
    ap.add_argument("--seed", required=True)
    ap.add_argument("--out-root", required=True)
    ap.add_argument("--poll", type=int, default=120)
    args = ap.parse_args()
    out = Path(args.out_root) / args.job_id
    out.mkdir(parents=True, exist_ok=True)
    job = wait(args.job_id, out, args.poll)
    (out / "job.json").write_text(json.dumps({k: v for k, v in job.items() if not k.endswith("SignedUrl")}, indent=1) + "\n")
    fetch(job.get("metricsFileSignedUrl"), out / "metrics.jsonl")
    fetch(job.get("renderSamplesSignedUrl"), out / "render-samples.jsonl")
    if job["state"] not in DONE:
        log(out, f"JOB ENDED WITHOUT ADAPTER: {job['state']} {(job.get('status') or {}).get('message', '')}")
        sys.exit(1)
    model = job.get("outputModel", "").split("/")[-1]
    stage = STAGE / args.job_id
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    log(out, f"download {model}")
    sh(["firectl", "-a", "oneiron", "model", "download", model, f"{stage}/", "--quiet"])
    cfg = next(stage.rglob("adapter_config.json"))
    files = [cfg.parent / f for f in KEEP_FILES if (cfg.parent / f).exists()] + sorted(cfg.parent.glob("adapter_model-*-of-*.safetensors"))
    shas = {f.name: sha256(f) for f in files}
    remote = f"{REMOTE_ROOT}/{args.job_id}"
    for host in HOSTS:
        sh(["ssh", host, f"mkdir -p {remote}/adapter"])
        sh(["rsync", "-a", *map(str, files), f"{host}:{remote}/adapter/"])
        got = dict(line.split()[::-1] for line in sh(["ssh", host, f"cd {remote}/adapter && sha256sum " + " ".join(shas)]).splitlines())
        got = {Path(k).name: v for k, v in got.items()}
        if got != shas:
            log(out, f"SHA MISMATCH on {host}: keep the Mac copy {stage}")
            sys.exit(1)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(DELETED_LOG, "a") as f:
        for p in files:
            f.write(json.dumps({"path": str(p), "bytes": p.stat().st_size, "sha256": shas[p.name],
                                "mel": f"mel:{remote}/adapter/{p.name}", "nelly": f"nelly:{remote}/adapter/{p.name}",
                                "freed_at": now}) + "\n")
    shutil.rmtree(stage)
    log(out, f"adapter on nelly + mel {remote}/adapter (sha checked on both), Mac copy deleted")
    # one of the world's own fact questions for the apply check
    rows = [json.loads(line) for line in open(Path(args.dataset_dir) / f"{args.world}-s1.jsonl")]
    q = next(r for r in rows if r["meta_form"] == "qa_forward")
    question = [{"role": "user", "content": q["messages"][1]["content"]}]
    qfile = out / "apply-question.json"
    qfile.write_text(json.dumps(question))
    sh(["rsync", "-a", str(qfile), f"nelly:{remote}/apply-question.json"])
    host_out = sh(["ssh", "nelly", f"bash {REMOTE_ROOT}/check/fireworks_convert_host.sh {remote} {remote}/apply-question.json"])
    (out / "host.log").write_text(host_out)
    for name in ("gguf-verify.json", "apply-check.json"):
        sh(["rsync", "-a", f"nelly:{remote}/{name}", str(out / name)])
    verify = json.loads((out / "gguf-verify.json").read_text())
    apply = json.loads((out / "apply-check.json").read_text())
    gguf_sha = next(line.split()[0] for line in host_out.splitlines() if line.endswith("adapter.gguf"))
    # nelly cannot reach mel by name: stream through the Mac, nothing written on the Mac
    sh(f"ssh nelly 'cat {remote}/adapter.gguf' | ssh mel 'cat > {remote}/adapter.gguf'")
    mel_sha = sh(["ssh", "mel", f"sha256sum {remote}/adapter.gguf"]).split()[0]
    ok = verify.get("passed") and apply.get("changed") and mel_sha == gguf_sha
    metrics = [json.loads(line) for line in (out / "metrics.jsonl").read_text().splitlines() if line.strip()] \
        if (out / "metrics.jsonl").exists() else []
    loss = [m.get("train/loss") for m in metrics if "train/loss" in m]
    receipt = {
        "job": job.get("name"), "output_model": job.get("outputModel"), "world": args.world, "seed": args.seed,
        "state": job["state"], "created": job.get("createTime"), "completed": job.get("completedTime"),
        "settings": {k: job.get(k) for k in ("baseModel", "dataset", "epochs", "learningRate", "learningRateWarmupSteps", "lrScheduler",
                                             "batchSizeSamples", "loraRank", "loraAlpha", "optimizerWeightDecay", "maxContextLength",
                                             "rendererHuggingFaceRepoId")},
        "dataset_report": json.loads((Path(args.dataset_dir) / f"{args.world}-{args.seed}.report.json").read_text()),
        "adapter_sha256": shas, "gguf": {"nelly": f"nelly:{remote}/adapter.gguf", "mel": f"mel:{remote}/adapter.gguf", "sha256": gguf_sha},
        "gguf_verify": {k: verify.get(k) for k in ("modules_checked", "missing_in_gguf", "gguf_alpha", "worst_rel_err", "passed")},
        "apply_check": {"question": question[0]["content"], "trained_answer": q["messages"][2]["content"],
                        "base": apply.get("completion_scale0"), "adapter": apply.get("completion_scale1"), "changed": apply.get("changed")},
        "train_loss": {"steps": len(loss), "first": loss[0] if loss else None, "last": loss[-1] if loss else None},
        "differences_from_the_nights": DIFFERENCES, "landed": datetime.now().strftime("%Y-%m-%d %H:%M JST"), "passed": bool(ok)}
    (out / "receipt.json").write_text(json.dumps(receipt, indent=1) + "\n")
    with open(BUS, "a") as f:
        f.write(f"{datetime.now().strftime('%H:%M')} meta-fireworks: LANDED {args.job_id} ({args.world} {args.seed}, different recipe: "
                f"bf16 Fireworks SFT, no cd rows) gguf {gguf_sha[:8]} nelly+mel {remote}/adapter.gguf; verify "
                f"{verify.get('modules_checked')} modules {verify.get('worst_rel_err')}; applies on Q4_K_M: {apply.get('changed')}; "
                f"receipt {out}/receipt.json\n")
    log(out, f"LANDED passed={bool(ok)} gguf {gguf_sha[:8]}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
