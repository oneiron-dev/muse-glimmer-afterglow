#!/usr/bin/env bash
# Host side of a Fireworks SFT landing (run on nelly, CPU only): convert the downloaded PEFT adapter to a GGUF LoRA with
# the nights' converter, check it module by module (gguf-verify), and prove it loads on the served Q4_K_M base and changes
# greedy output on one of the world's own questions. One landing at a time (flock), so two llama-servers never share CPU.
# Usage: night/fireworks_convert_host.sh JOB_DIR QUESTION_JSON
#   JOB_DIR holds adapter/ (adapter_config.json + adapter_model-00001-of-00001.safetensors); writes adapter.gguf,
#   gguf-verify.json, apply-check.json and convert.log there.
set -euo pipefail
JOB=$1; QUESTION=$2
REPO=/data/meta-night/repo
CHECK=/data/fireworks/check
PY=/data/drafter-tune-20261014/venv/bin/python
BASE_CFG=/data/drafter-tune-20261014/models/target-nf4
BASE_GGUF=/data/drafter-tune-20261014/models/official-gguf/Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf
exec 9>/data/fireworks/landing.lock
flock 9
cd "$JOB/adapter"
shard=$(ls adapter_model-*-of-*.safetensors | head -1)
[ "$(ls adapter_model-*-of-*.safetensors | wc -l)" = 1 ] || { echo "expected one adapter shard" >&2; exit 2; }
ln -sf "$shard" adapter_model.safetensors
cd "$REPO"
nice -n 10 bash night/convert_adapter.sh "$JOB/adapter" "$JOB/adapter.gguf" "$BASE_CFG" "$PY" > "$JOB/convert.log" 2>&1
PYTHONPATH="$REPO/.deps/llama.cpp/gguf-py" nice -n 10 "$PY" "$CHECK/night/verify_gguf_lora.py" --adapter "$JOB/adapter" \
  --gguf "$JOB/adapter.gguf" --base-config "$BASE_CFG/config.json" --out "$JOB/gguf-verify.json" | tail -1
bash "$CHECK/lora_apply_check.sh" /data/meta-night/llama.cpp "$BASE_GGUF" "$JOB/adapter.gguf" "$JOB/apply-check.json" 18690 "$QUESTION" | tail -1
sha256sum "$JOB/adapter.gguf"
echo HOST-LANDING-DONE
