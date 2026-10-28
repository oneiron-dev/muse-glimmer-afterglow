#!/usr/bin/env bash
# Train the decision LoRA (arm D1) through fleet run, convert it to GGUF and verify it, then fetch the small outputs.
# Usage: [HOST=mel|nelly] [PY=...] [MODEL=...] [LLAMA=...] decide/train_d1.sh [LABEL] [EPOCHS]
# The defaults are mel's paths; on another host pass the paths Meta night gives with its go.
# The trainer is the entry's own (night/train_adapter.py) at the R2 recipe (r16, attention + MLP, all layers, LR 1e-4);
# the rows are long (every note is an option), so 2 rows per micro-batch and 8 accumulation steps.
set -u
LABEL=${1:-d1}; EPOCHS=${2:-1}
HERE="$(cd "$(dirname "$0")/.." && pwd)"
HOST=${HOST:-mel}
REMOTE=${REMOTE:-/data/meta-decide}
PY=${PY:-/home/lexi/c-pilot-gpu-venv-20261014/bin/python}
MODEL=${MODEL:-/home/lexi/c-measure-gpu-20261014/models}
LLAMA=${LLAMA:-/data/meta-night/llama.cpp}
OUT=$REMOTE/out/$LABEL
LOCAL=$HERE/eval/results/2026-10-29-decision/train-$LABEL
mkdir -p "$LOCAL"
ssh $HOST "mkdir -p $REMOTE/repo $REMOTE/out" && \
rsync -a --delete --exclude .git --exclude runs --exclude .deps --exclude '__pycache__' "$HERE/" $HOST:$REMOTE/repo/ && \
fleet run --who meta-decide --goal "afterglow decision LoRA $LABEL" --need cuda,gpu-4090 --host $HOST --mem 18G --cwd $REMOTE/repo -- \
  timeout --kill-after=20s 40m bash -c "set -e; \
  $PY night/train_adapter.py --model-dir $MODEL --train decide/data/train-decision.jsonl --out-dir $OUT \
    --rank 16 --targets attn_mlp --layers all --epochs $EPOCHS --save-epochs $EPOCHS --lr 1e-4 --batch-rows 2 --grad-accum 8 --max-len 1300; \
  A=\$(ls -d $OUT/epoch$EPOCHS 2>/dev/null || echo $OUT); \
  LLAMA_CPP_DIR=$LLAMA night/convert_adapter.sh \$A $OUT/decision-$LABEL.gguf $MODEL $PY; \
  PYTHONPATH=$LLAMA/gguf-py $PY night/verify_gguf_lora.py --adapter \$A --gguf $OUT/decision-$LABEL.gguf \
    --base-config $MODEL/config.json --out $OUT/gguf-verify.json; \
  sha256sum $OUT/decision-$LABEL.gguf > $OUT/decision-$LABEL.gguf.sha256" > "$LOCAL/job.log" 2>&1
echo "exit=$?" >> "$LOCAL/job.log"
rsync -a --exclude 'epoch*' --exclude '*.safetensors' --exclude '*.gguf' $HOST:$OUT/ "$LOCAL/"
tail -5 "$LOCAL/job.log"
