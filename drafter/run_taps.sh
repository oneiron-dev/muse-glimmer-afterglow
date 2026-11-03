#!/usr/bin/env bash
# Taps for the four worlds (served target = NF4 Glimmer + the world's kept adapter), days 1-10, then the stock
# drafter's teacher-forced acceptance on every world's held-out day 10.
set -euo pipefail
W=/data/drafter
PY=/opt/drafter/venv/bin/python
for w in w1-ines-varga w2-solo-developer w3-shop-owner w4-grad-student; do
  $PY $W/scripts/extract_taps.py $W --world $w --adapter $W/adapters/$w/peft --days 1-10 --alt-days 10
done
$PY $W/scripts/train_drafter.py $W --val all:10 --eval-only --out results/stock-day10
echo RUN-TAPS-DONE
