#!/usr/bin/env bash
# Convert a PEFT LoRA adapter for Muse Glimmer to a GGUF LoRA for llama-server --lora.
# Usage: night/convert_adapter.sh ADAPTER_DIR OUT_GGUF BASE_CONFIG_DIR [PYTHON]
#   BASE_CONFIG_DIR: any directory holding the Glimmer config.json (weights are not read). Only its config.json is
#   used, without quantization_config: the converter otherwise tries to dequantize the NF4 base (unsupported).
# Uses llama.cpp v0.7.0 (620d2225) in .deps/llama.cpp (cloned here if missing) with
# night/llamacpp/muse-glimmer-lora.patch applied: the stock converter crashes on Q/K LoRA
# (`.ndim` on LoraTorchTensor) and would emit QK-norm weights into the adapter.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ADAPTER=$1; OUT=$2; BASE=$3; PY=${4:-python3}
LLAMA_REV=620d2225cc436ab0b001e3200e590e3b286ef9ab
LLAMA="${LLAMA_CPP_DIR:-$ROOT/.deps/llama.cpp}"
if [ ! -d "$LLAMA/.git" ]; then
  git clone --quiet --filter=blob:none https://github.com/ggml-org/llama.cpp.git "$LLAMA"
fi
if [ "$(git -C "$LLAMA" rev-parse HEAD)" != "$LLAMA_REV" ]; then
  git -C "$LLAMA" fetch --quiet --depth 1 origin "$LLAMA_REV"
  git -C "$LLAMA" checkout --quiet "$LLAMA_REV"
fi
if git -C "$LLAMA" apply --check "$ROOT/night/llamacpp/muse-glimmer-lora.patch" 2>/dev/null; then
  git -C "$LLAMA" apply "$ROOT/night/llamacpp/muse-glimmer-lora.patch"
elif ! git -C "$LLAMA" apply --reverse --check "$ROOT/night/llamacpp/muse-glimmer-lora.patch" 2>/dev/null; then
  echo "patch neither applies nor is applied in $LLAMA" >&2; exit 2
fi
mkdir -p "$(dirname "$OUT")"
CFG=$(mktemp -d)
trap 'rm -rf "$CFG"' EXIT
"$PY" -c 'import json,sys; c=json.load(open(sys.argv[1])); c.pop("quantization_config",None); json.dump(c,open(sys.argv[2],"w"),indent=1)' \
  "$BASE/config.json" "$CFG/config.json"
PYTHONPATH="$LLAMA/gguf-py${PYTHONPATH:+:$PYTHONPATH}" "$PY" "$LLAMA/convert_lora_to_gguf.py" "$ADAPTER" \
  --base "$CFG" --outfile "$OUT" --outtype f16
sha256sum "$OUT" 2>/dev/null || shasum -a 256 "$OUT"
