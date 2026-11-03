#!/usr/bin/env bash
# Stage 2 on the box: the final drafters with the recipe the pilot picked (LR, EPOCHS), each trained on days 1-9 and
# checked teacher-forced on every world's held-out day 10; then each converts to the serving GGUF.
#   own-<world>: that world's traffic only;  pooled: the four worlds' traffic together.
# usage: LR=2e-5 EPOCHS=2 run_stage2.sh [own|pooled|all|pooled-only|own-<w>-only WORLD]
set -euo pipefail
W=/data/drafter
PY=/opt/drafter/venv/bin/python
LR=${LR:?}; EPOCHS=${EPOCHS:?}; WHICH=${1:-all}
LRG=$(python3 -c "print(f'{float(\"$LR\"):g}')")   # the trainer names its output with the %g form
train() {  # NAME TRAIN_SPEC
  local name=$1 spec=$2
  if [ ! -f $W/ckpt/$name-lr$LRG/model.safetensors ]; then
    $PY $W/scripts/train_drafter.py $W --train "$spec" --val all:10 --lrs $LR --epochs $EPOCHS --eval-every-epoch \
      --save --out ckpt/$name
  fi
  [ -f $W/gguf/$name-Q4_K_M.gguf ] || $W/scripts/convert.sh $W/ckpt/$name-lr$LRG $name
  echo "STAGE2 $name DONE"
}
if [ "$WHICH" = own ] || [ "$WHICH" = all ]; then
  for w in w1-ines-varga w2-solo-developer w3-shop-owner w4-grad-student; do train own-${w%%-*} $w:1-9; done
fi
if [ "$WHICH" = pooled ] || [ "$WHICH" = all ] || [ "$WHICH" = pooled-only ]; then train pooled all:1-9; fi
case $WHICH in own-*-only) train ${WHICH%-only} ${2:?WORLD}:1-9 ;; esac
echo STAGE2-DONE
