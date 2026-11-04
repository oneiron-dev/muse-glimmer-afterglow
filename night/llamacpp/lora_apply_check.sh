#!/usr/bin/env bash
# Prove a GGUF LoRA loads on the served Q4_K_M base and changes greedy output (CPU only, GPU hidden; no serving).
# Starts llama-server on 127.0.0.1 with the adapter loaded at scale 0, asks one prompt at scale 0 and at scale 1,
# writes both completions and the server's adapter list to OUT_JSON, then stops the server.
# Usage: night/llamacpp/lora_apply_check.sh LLAMA_DIR BASE_GGUF ADAPTER_GGUF OUT_JSON [PORT] [PROMPT_FILE]
set -euo pipefail
LLAMA=$1; BASE=$2; LORA=$3; OUT=$4; PORT=${5:-18690}; PROMPT_FILE=${6:-}
LOG="${OUT%.json}.server.log"
CUDA_VISIBLE_DEVICES= nice -n 10 "$LLAMA/build/bin/llama-server" -m "$BASE" --lora "$LORA" --lora-init-without-apply \
  -ngl 0 -t 8 -c 2048 -np 1 --host 127.0.0.1 --port "$PORT" > "$LOG" 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null || true' EXIT
for _ in $(seq 1 180); do
  curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
  kill -0 $PID 2>/dev/null || { echo "server exited; see $LOG" >&2; exit 2; }
  sleep 2
done
python3 - "$PORT" "$OUT" "$PROMPT_FILE" <<'EOF'
import json, sys, urllib.request
port, out, prompt_file = sys.argv[1], sys.argv[2], sys.argv[3]
root = f"http://127.0.0.1:{port}"
def post(path, body):
    req = urllib.request.Request(root + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=900).read())
msgs = json.loads(open(prompt_file).read()) if prompt_file else [{"role": "user", "content": "What is my weekend charge?"}]
prompt = post("/apply-template", {"messages": msgs, "chat_template_kwargs": {"reasoning_strength": "minimal"}})["prompt"]
adapters = json.loads(urllib.request.urlopen(root + "/lora-adapters", timeout=30).read())
res = {}
for scale in (0.0, 1.0):
    r = post("/completion", {"prompt": prompt, "n_predict": 24, "temperature": 0.0, "top_k": 1, "seed": 1, "cache_prompt": False,
                             "lora": [{"id": 0, "scale": scale}]})
    res[str(scale)] = r["content"]
report = {"adapters": adapters, "prompt_tail": prompt[-200:], "completion_scale0": res["0.0"], "completion_scale1": res["1.0"],
          "changed": res["0.0"] != res["1.0"]}
open(out, "w").write(json.dumps(report, indent=1) + "\n")
print(json.dumps({"changed": report["changed"], "scale0": res["0.0"][:120], "scale1": res["1.0"][:120]}))
EOF
