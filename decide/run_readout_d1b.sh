#!/usr/bin/env bash
# Usage: PY_CLIENT=<python with jinja2 + tokenizers> [SLOT=0] [SERVER=...] [MODEL_DIR=...] decide/run_readout_d1b.sh
cd "$(dirname "$0")/.."
OUT=eval/results/2026-10-29-decision/readout-D1b.jsonl
for f in route-test duplicate-test supersede-test supersede-day2-check supersede-day3-check duplicate-day3-check route-calibration duplicate-calibration supersede-calibration; do
  ${PY_CLIENT:?set PY_CLIENT to a python with jinja2 + tokenizers} decide/readout.py --server ${SERVER:-http://100.88.57.107:18610} \
    --model-dir ${MODEL_DIR:-/Users/olety/models/mlx/Muse-Glimmer-30B-4bit} --rows decide/data/$f.jsonl --slot ${SLOT:-0} --adapter decision-d1b.gguf --stop-file ${STOP_FILE:-/tmp/STOP-D1b} --out $OUT > /dev/null || { echo "FAILED $f"; exit 1; }
  echo "done $f $(wc -l < $OUT) $(date +%H:%M:%S)"
done
echo ALL_DONE
