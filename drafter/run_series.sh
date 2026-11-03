#!/usr/bin/env bash
# The nightly loop for one world: night k continues from night k-1's drafter (night 1 from stock), trains on day k's
# traffic only, and is checked teacher-forced on day k+1 (the next morning's turns), next to the stock drafter and the
# previous night's drafter on the same day. Only the latest night's weights stay on disk.
# usage: LR=2e-5 EPOCHS=2 run_series.sh WORLD [FIRST_NIGHT] [LAST_NIGHT]
set -euo pipefail
W=/data/drafter
PY=/opt/drafter/venv/bin/python
LR=${LR:?}; EPOCHS=${EPOCHS:?}; WORLD=$1; FIRST=${2:-1}; LAST=${3:-9}
LRG=$(python3 -c "print(f'{float(\"$LR\"):g}')")
S=${WORLD%%-*}
mkdir -p $W/results/series-$S
prev=models/drafter-stock
for k in $(seq $FIRST $LAST); do
  next=$((k + 1))
  $PY $W/scripts/train_drafter.py $W --val $WORLD:$next --eval-only --out results/series-$S/stock-day$next
  $PY $W/scripts/train_drafter.py $W --train $WORLD:$k --val $WORLD:$next --init $prev --lrs $LR --epochs $EPOCHS \
    --save --out ckpt/series-$S-night$k
  cp $W/ckpt/series-$S-night$k-lr$LRG/train_report.json $W/results/series-$S/night$k.json
  [ "$prev" = models/drafter-stock ] || rm -rf $W/$prev
  prev=ckpt/series-$S-night$k-lr$LRG
  echo "SERIES $S night $k DONE"
done
echo SERIES-DONE $S
