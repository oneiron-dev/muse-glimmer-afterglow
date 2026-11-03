#!/usr/bin/env bash
# After the main chain (runall) ends: the near-tie readout at every drafter-vs-stock split (target alone with each
# world's adapter), then the nightly series for the four worlds.
set -uo pipefail
W=/data/drafter
S=$W/scripts
R=/opt/drafter/llama.cpp/build/bin
T=$W/models/gguf/Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf
export LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu:/usr/local/cuda/lib64:$R
until [ -f $W/logs/runall.rc ]; do sleep 30; done
echo "runall rc $(cat $W/logs/runall.rc)"
for w in w1-ines-varga w2-solo-developer w3-shop-owner w4-grad-student; do
  $R/llama-server -m $T -ngl 99 -fa on -np 1 -c 16384 --no-webui --cache-ram 0 \
    --lora-scaled $W/adapters/$w/adapter.gguf:0 --lora-init-without-apply --host 127.0.0.1 --port 18441 \
    > $W/results/live/server-tie-$w.log 2>&1 &
  SP=$!
  for i in $(seq 1 300); do curl -sf http://127.0.0.1:18441/health >/dev/null && break; sleep 1; done
  /opt/drafter/venv/bin/python $S/tie_check.py 18441 $W/results/live $w --ref stock-ours
  kill $SP; wait $SP 2>/dev/null
done
echo TIE-DONE
for w in w1-ines-varga w2-solo-developer w3-shop-owner w4-grad-student; do
  LR=2e-5 EPOCHS=2 $S/run_series.sh $w 1 9 || echo "SERIES $w FAILED"
done
echo AFTER-DONE
