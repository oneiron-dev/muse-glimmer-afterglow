#!/usr/bin/env bash
# Start llama-server with the target GGUF and one LoRA adapter (loaded at scale 0), run the 4-arm eval, stop the server.
# Runs on the GPU host inside one fleet booking. Target-only decoding (no drafter) for scoring.
# Usage: night/llamacpp/serve_and_eval.sh LLAMA_SERVER TARGET_GGUF ADAPTER_GGUF LABEL OUT_DIR -- <eval/llama_eval.py args>
# Env: PY (python with transformers), MODEL_DIR (HF dir with tokenizer + chat_template.jinja), PORT (default 18620),
#      SERVER_EXTRA (more llama-server flags, e.g. "--control-vector-scaled <gguf> <scale>"),
#      CHANNEL_CHECK_ARGS, TOOL_CHECK_ARGS (if set: eval/channel_check.py / eval/tool_call_check.py against the same
#      server after the eval, with these args).
set -euo pipefail
SERVER=$1; TARGET=$2; ADAPTER=$3; LABEL=$4; OUT=$5; shift 5; [ "${1:-}" = "--" ] && shift
PORT=${PORT:-18620}
mkdir -p "$OUT"
"$SERVER" -m "$TARGET" --lora "$ADAPTER" --lora-init-without-apply --cache-ram 0 -ngl 99 -fa on -np 4 -c 32768 \
  --host 127.0.0.1 --port "$PORT" ${SERVER_EXTRA:-} > "$OUT/llama-server-$LABEL.log" 2>&1 &
SPID=$!
trap 'kill $SPID 2>/dev/null; wait $SPID 2>/dev/null || true' EXIT
for _ in $(seq 1 180); do
  if curl -sf "http://127.0.0.1:$PORT/health" > /dev/null; then break; fi
  kill -0 $SPID 2>/dev/null || { echo "llama-server exited during load"; tail -30 "$OUT/llama-server-$LABEL.log"; exit 3; }
  sleep 2
done
curl -sf "http://127.0.0.1:$PORT/lora-adapters"; echo
"$PY" eval/llama_eval.py --server "http://127.0.0.1:$PORT" --model-dir "$MODEL_DIR" --label "$LABEL" --out-dir "$OUT" "$@"
if [ -n "${CHANNEL_CHECK_ARGS:-}" ]; then
  "$PY" eval/channel_check.py --server "http://127.0.0.1:$PORT" $CHANNEL_CHECK_ARGS
fi
if [ -n "${TOOL_CHECK_ARGS:-}" ]; then
  "$PY" eval/tool_call_check.py --server "http://127.0.0.1:$PORT" $TOOL_CHECK_ARGS
fi
