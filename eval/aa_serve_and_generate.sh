#!/usr/bin/env bash
# AA-Omniscience-Public generation on the GPU host: one llama-server with every night adapter loaded at scale 0,
# then GENERATOR generate for base and each requested adapter (scale 1). Target-only, greedy.
# Usage: eval/aa_serve_and_generate.sh LLAMA_SERVER TARGET_GGUF OUT_DIR ADAPTER_GGUF... -- label=id ...
# Env: PY (python with transformers), MODEL_DIR (tokenizer + chat_template.jinja), PORT (default 18640),
#      GENERATOR (default eval/aa_omniscience.py; eval/simpleqa_gate.py for the abstention gate slice).
set -euo pipefail
SERVER=$1; TARGET=$2; OUT=$3; shift 3
LORA=(); while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do LORA+=(--lora-scaled "$1:0"); shift; done
[ "${1:-}" = "--" ] && shift
PORT=${PORT:-18640}
mkdir -p "$OUT"
"$SERVER" -m "$TARGET" "${LORA[@]}" --cache-ram 0 -ngl 99 -fa on -np 4 -c 16384 --host 127.0.0.1 --port "$PORT" \
  > "$OUT/llama-server.log" 2>&1 &
SPID=$!
trap 'kill $SPID 2>/dev/null; wait $SPID 2>/dev/null || true' EXIT
for _ in $(seq 1 180); do
  if curl -sf "http://127.0.0.1:$PORT/health" > /dev/null; then break; fi
  kill -0 $SPID 2>/dev/null || { echo "llama-server exited during load"; tail -30 "$OUT/llama-server.log"; exit 3; }
  sleep 2
done
curl -sf "http://127.0.0.1:$PORT/lora-adapters"; echo
"$PY" "${GENERATOR:-eval/aa_omniscience.py}" generate --server "http://127.0.0.1:$PORT" --model-dir "$MODEL_DIR" --out-dir "$OUT" --arms "$@"
