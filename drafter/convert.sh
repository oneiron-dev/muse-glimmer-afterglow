#!/usr/bin/env bash
# HF drafter dir -> GGUF bf16 -> Q4_K_M with llama.cpp 620d2225 (the serving build). The quant copies the official
# dflash-Muse-Glimmer-30B-Q4_K_M.gguf layout (step A): Q4_K, with attn_v and ffn_down at Q6_K.
# usage: convert.sh HF_DRAFTER_DIR NAME   -> /data/drafter/gguf/NAME-Q4_K_M.gguf (and its tensor-type counts)
set -euo pipefail
W=/data/drafter
LC=/opt/drafter/llama.cpp
export LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu:/usr/local/cuda/lib64:$LC/build/bin
mkdir -p $W/gguf
/opt/drafter/venv/bin/python $LC/convert_hf_to_gguf.py "$1" --target-model-dir $W/models/target-nf4 --outtype bf16 \
  --outfile $W/gguf/$2-bf16.gguf 2>&1 | grep -v Writing | tail -2
rm -f $W/gguf/$2-Q4_K_M.gguf
$LC/build/bin/llama-quantize --tensor-type attn_v=q6_k --tensor-type ffn_down=q6_k \
  $W/gguf/$2-bf16.gguf $W/gguf/$2-Q4_K_M.gguf Q4_K_M 2>&1 | tail -1
rm -f $W/gguf/$2-bf16.gguf
/opt/drafter/venv/bin/python - "$W/gguf/$2-Q4_K_M.gguf" <<'PY'
import sys, collections
from gguf import GGUFReader
r = GGUFReader(sys.argv[1])
print(sys.argv[1], dict(collections.Counter(t.tensor_type.name for t in r.tensors)))
PY
sha256sum $W/gguf/$2-Q4_K_M.gguf
