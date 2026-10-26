#!/usr/bin/env bash
# Train one sweep arm on the GPU host and score its saved epochs (arm B; arm D too with SCORE_NOTES_ARM=1).
# Usage (on the GPU host, inside a fleet booking): night/sweep_arm.sh ARM TRAIN_JSONL SAVE_EPOCHS -- <train_adapter.py args>
# Env: PY (python with unsloth/peft), MODEL_DIR (NF4 checkpoint), OUT_ROOT (outputs), WORLD, NOTES (consolidated.json).
set -euo pipefail
ARM=$1; TRAIN=$2; SAVE_EPOCHS=$3; shift 3
[ "${1:-}" = "--" ] && shift
OUT="$OUT_ROOT/$ARM"
mkdir -p "$OUT"
"$PY" night/train_adapter.py --model-dir "$MODEL_DIR" --train "$TRAIN" --out-dir "$OUT" --save-epochs "$SAVE_EPOCHS" "$@"
ADAPTERS=()
for e in ${SAVE_EPOCHS//,/ }; do ADAPTERS+=("${ARM}e${e}=$OUT/epoch${e}"); done
EXTRA=()
[ "${SCORE_NOTES_ARM:-0}" = "1" ] && EXTRA+=(--adapter-notes-arm)
"$PY" eval/score_peft.py --model-dir "$MODEL_DIR" --world "$WORLD" --notes "$NOTES" --adapters "${ADAPTERS[@]}" --out-dir "$OUT" "${EXTRA[@]}"
