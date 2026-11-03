#!/usr/bin/env bash
# Live eval in llama.cpp 620d2225 at the serving flags: one world's held-out day, arms back to back, the world's
# adapter loaded as served (--lora-scaled at 0, scale 1 sent per request), greedy, concurrency 1, one slot.
# usage: run_live.sh OUTDIR WORLD DAY ARM=DRAFTER_GGUF|none ...
set -uo pipefail
W=/data/drafter
R=/opt/drafter/llama.cpp/build/bin
T=$W/models/gguf/Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf
export LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu:/usr/local/cuda/lib64:$R
OUT=$W/$1; WORLD=$2; DAY=$3; shift 3
PORT=18440
mkdir -p $OUT
SEQ=$W/seqs/$WORLD/day$(printf %02d $DAY).json
WARM=$W/seqs/$WORLD/day$(printf %02d $((DAY - 1))).json
LORA=$W/adapters/$WORLD/adapter.gguf
nvidia-smi --query-gpu=name,power.limit,temperature.gpu --format=csv,noheader > $OUT/gpu-$WORLD.txt
for spec in "$@"; do
  arm=${spec%%=*}; d=${spec#*=}
  if [ "$d" = none ]; then DR=(); else
    DR=(-md $d -ngld all --spec-type draft-dflash --spec-draft-n-max 15 --spec-draft-n-min 0 --spec-draft-p-min 0
        --spec-draft-sampling greedy)
  fi
  t0=$(date +%s)
  $R/llama-server -m $T -ngl 99 -fa on -np 1 -c 16384 --metrics --no-webui --cache-ram 0 \
    --lora-scaled $LORA:0 --lora-init-without-apply --host 127.0.0.1 --port $PORT "${DR[@]}" \
    > $OUT/server-$WORLD-$arm.log 2>&1 &
  SP=$!
  for i in $(seq 1 300); do curl -sf http://127.0.0.1:$PORT/health >/dev/null && break; sleep 1; done
  ( while sleep 2; do nvidia-smi --query-gpu=memory.used,power.draw --format=csv,noheader,nounits; done ) > $OUT/gpu-$WORLD-$arm.log &
  VP=$!
  /opt/drafter/venv/bin/python $W/scripts/eval_live.py $PORT $arm $SEQ $WARM $OUT/$WORLD-$arm.jsonl --n-predict 512
  kill $VP; kill $SP; wait $SP 2>/dev/null
  echo "$WORLD $arm peak_vram_mib $(cut -d, -f1 $OUT/gpu-$WORLD-$arm.log | sort -n | tail -1) wall_s $(( $(date +%s) - t0 ))" | tee -a $OUT/arms.txt
done
echo LIVE-DONE $WORLD
