#!/usr/bin/env bash
# Stage 1 on the box: taps for the worlds not yet done, the stock drafter's teacher-forced acceptance on every
# world's held-out day 10, then the recipe pilot on w1 (train days 1-8, check on day 9; two learning rates, three
# epochs, the check after each epoch).
set -euo pipefail
W=/data/drafter
PY=/opt/drafter/venv/bin/python
for w in w1-ines-varga w2-solo-developer w3-shop-owner w4-grad-student; do
  [ -f $W/taps/$w/day10.pt ] || $PY $W/scripts/extract_taps.py $W --world $w --adapter $W/adapters/$w/peft --days 1-10 --alt-days 10
done
echo STAGE1-TAPS-DONE
$PY $W/scripts/train_drafter.py $W --val all:10 --eval-only --out results/stock-day10
echo STAGE1-STOCK-DONE
$PY $W/scripts/train_drafter.py $W --train w1-ines-varga:1-8 --val w1-ines-varga:9 --lrs 2e-5,6e-5 --epochs 3 \
  --eval-every-epoch --out ckpt/pilot-w1
echo STAGE1-DONE
