#!/usr/bin/env bash
# The whole test after the pilot, unattended: per world, train its own drafter (days 1-9), convert it, then the live
# llama.cpp eval of that world's held-out day 10 with the baseline arms and its own drafter; then the pooled drafter
# (four worlds, days 1-9) and its live arm on every world; then the summary.
# usage: LR=2e-5 EPOCHS=3 run_all.sh
set -uo pipefail
W=/data/drafter
S=$W/scripts
LR=${LR:?}; EPOCHS=${EPOCHS:?}
G=$W/gguf
OFFICIAL=$W/models/gguf/dflash-Muse-Glimmer-30B-Q4_K_M.gguf
for w in w1-ines-varga w2-solo-developer w3-shop-owner w4-grad-student; do
  s=${w%%-*}
  LR=$LR EPOCHS=$EPOCHS $S/run_stage2.sh own-$s-only "$w" || { echo "STAGE2 own-$s FAILED"; continue; }
  arms=(none=none stock-official=$OFFICIAL stock-ours=$G/stock-ours-Q4_K_M.gguf own=$G/own-$s-Q4_K_M.gguf)
  [ -f $G/dflash-stepA-generic-Q4_K_M.gguf ] && arms+=(stepA-generic=$G/dflash-stepA-generic-Q4_K_M.gguf)
  $S/run_live.sh results/live $w 10 "${arms[@]}"
done
LR=$LR EPOCHS=$EPOCHS $S/run_stage2.sh pooled-only || echo "STAGE2 pooled FAILED"
if [ -f $G/pooled-Q4_K_M.gguf ]; then
  for w in w1-ines-varga w2-solo-developer w3-shop-owner w4-grad-student; do
    $S/run_live.sh results/live $w 10 pooled=$G/pooled-Q4_K_M.gguf
  done
fi
/opt/drafter/venv/bin/python $S/analyze_live.py $W/results/live
echo RUN-ALL-DONE
