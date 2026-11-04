#!/bin/bash
# Custom voices, the GPU part (one job for one or more voices; ~25-40 min on one 4090, whole card):
#   1. extract   re:dvx contrast states on the NF4 base for the five standard axes and every voice axis
#   2. fit       re:dvx directions -> llama.cpp control-vector GGUFs (fit_vectors.py; the "raw" variant is served).
#                A voice axis is fitted with re:dvx's diff_of_means (the mean paired difference) unless its axis.json
#                names another "estimator": centered PCA gave a different direction for each contrast set written from
#                the same sentence (cosine 0.16-0.41), the mean difference the same one (0.85-0.90).
#   3. check     the behaviour check with each voice's vector added at each check scale (behaviour_check.py --vector)
#   4. serve     the per-request control-vector llama-server with every voice loaded at 0 (layers 16-42)
#   5. sweep     voice_sweep.py: test-prompt replies per scale + the "I don't know" rate on the 200 gate questions
# Usage (on the GPU host, from the repo copy): steering/voice_job.sh RUN_NAME SLUG [SLUG ...]
# Env: ROOT (default /data/meta-voice), MODEL_DIR, GGUF, LLAMA, PORT, LAYERS ("16 42", the served range), NGL,
#      SKIP_RAW=1 (load the voice vectors but sweep no raw arm), SWEEP_ARGS (more voice_sweep.py args),
#      EXTRA (NAME=PATH,... more vectors after the voices), COMBOS (voice_sweep.py --combos JSON),
#      PREP (default "extract,fit,check"; empty = reuse the run's vectors), VARIANT (comma list of raw, orth: the voice GGUFs
#      served), NP (server slots and client threads, default 4), OUT (sweep output dir name, default sweep), CHECK_SCALES, SWEEP_SCALES, IDK_SCALES, SWEEP_STEPS, DIALS_DIR (the served dial GGUFs <axis>-j1.gguf: the
#      presets in steering/voices.json get the same sweep and "I don't know" check; empty = voices only).
set -euo pipefail
RUN_NAME=$1; shift
ROOT=${ROOT:-/data/meta-voice}
PY=${PY:-$ROOT/venv/bin/python}
MODEL_DIR=${MODEL_DIR:-/data/drafter-tune-20261014/models/target-nf4}
GGUF=${GGUF:-/data/drafter-tune-20261014/models/official-gguf/Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf}
LLAMA=${LLAMA:-/data/afterglow/llama-v0.7.0-620d222-cv/bin/llama-server}
PORT=${PORT:-18670}
LAYERS=${LAYERS:-16 42}
NGL=${NGL:-99}
SWEEP_STEPS=${SWEEP_STEPS-sweep,idk}
DIALS_DIR=${DIALS_DIR-$ROOT/dials}
PREP=${PREP-extract,fit,check}
VARIANT=${VARIANT:-raw}
OUT=${OUT:-sweep}
EXTRA=${EXTRA:-}
COMBOS=${COMBOS:-}
SKIP_RAW=${SKIP_RAW:-0}
SWEEP_ARGS=${SWEEP_ARGS:-}
NP=${NP:-4}
CHECK_SCALES=${CHECK_SCALES:-0.04,0.08}
SWEEP_SCALES=${SWEEP_SCALES:-0.02,0.04,0.06,0.08}
IDK_SCALES=${IDK_SCALES:-0.04,0.06,0.08}
RUN=$ROOT/runs/$RUN_NAME
mkdir -p "$RUN"
log() { echo "$(date +%H:%M:%S) $*" | tee -a "$RUN/job.log"; }
log "start run=$RUN voices=$*"

"$PY" - "$RUN/axes.json" "$@" <<'PY'
import json, sys
from pathlib import Path
out, slugs = sys.argv[1], sys.argv[2:]
spec = json.loads(Path("steering/axes.json").read_text())
for slug in slugs:
    a = json.loads(Path(f"steering/voices/{slug}/axis.json").read_text())
    spec["axes"].append({k: a[k] for k in ("name", "kind", "positive_label", "negative_label", "templates",
                                           "adjective_pairs", "prompts", "test_prompts", "check_labels")}
                        | {"estimator": a.get("estimator") or "diff_of_means"})
Path(out).write_text(json.dumps(spec, indent=1, ensure_ascii=False) + "\n")
print(json.dumps({"axes": [a["name"] for a in spec["axes"]]}))
PY

if [[ $PREP == *extract* ]] && { [ ! -f "$RUN/states/status.json" ] || ! grep -q '"finished": true' "$RUN/states/status.json"; }; then
  log "extract"
  "$PY" steering/extract_states.py --model-dir "$MODEL_DIR" --axes "$RUN/axes.json" --out "$RUN/states" \
    --deadline-s 1200 2>&1 | tee -a "$RUN/extract.log" | grep -v Warning || true
  grep -q '"finished": true' "$RUN/states/status.json" || { log "extract did not finish"; exit 2; }
fi
[[ $PREP == *fit* ]] && log "fit" && "$PY" steering/fit_vectors.py --states "$RUN/states" --axes "$RUN/axes.json" --out "$RUN/vectors" > "$RUN/fit.log" 2>&1

VEC_ARGS=()
for slug in "$@"; do
  for s in ${CHECK_SCALES//,/ }; do VEC_ARGS+=(--vector "voice-$slug@$s=$RUN/vectors/base/voice-$slug-raw.gguf:$s"); done
done
[[ $PREP == *check* ]] && log "behaviour check (${#VEC_ARGS[@]} args)" && "$PY" steering/behaviour_check.py --model-dir "$MODEL_DIR" --axes "$RUN/axes.json" --axes-states "$RUN/states" \
  --out "$RUN/check" --deadline-s 1200 --vector-layers "${LAYERS// /,}" "${VEC_ARGS[@]}" 2>&1 | tee -a "$RUN/check.log" | grep -v Warning || true

[ -n "$SWEEP_STEPS" ] || { log "DONE (no sweep steps)"; exit 0; }
CV=""; VOICE_ARGS=(); i=0
for variant in ${VARIANT//,/ }; do  # raw: output stem <slug>; any other variant: <slug>-<variant>
  for slug in "$@"; do
    stem=$slug; [ "$variant" = raw ] || stem=$slug-$variant
    CV="${CV:+$CV,}$RUN/vectors/base/voice-$slug-$variant.gguf:0"
    [ "$SKIP_RAW" = 1 ] || VOICE_ARGS+=(--voice "$stem=$i:$slug")
    i=$((i + 1))
  done
done
for extra in ${EXTRA//,/ }; do  # NAME=PATH: more vectors loaded after the voices (ids follow), for COMBOS
  CV="$CV,${extra#*=}:0"; echo "vector $i = ${extra%%=*}" | tee -a "$RUN/job.log"; i=$((i + 1))
done
[ -n "$COMBOS" ] && VOICE_ARGS+=(--combos "$COMBOS")
for a in $SWEEP_ARGS; do VOICE_ARGS+=("$a"); done  # e.g. "--instruction warm-instr=warm"
if [ -n "$DIALS_DIR" ]; then
  for axis in concise formal decisive; do
    CV="$CV,$DIALS_DIR/$axis-j1.gguf:0"; VOICE_ARGS+=(--dial "$axis=$i"); i=$((i + 1))
  done
  VOICE_ARGS+=(--presets steering/voices.json)
fi
log "serve"
"$LLAMA" -m "$GGUF" --alias muse-glimmer-30b -ngl "$NGL" -fa on -np "$NP" -c 16384 --jinja \
  --chat-template-kwargs '{"reasoning_strength":"minimal"}' --control-vector-scaled "$CV" \
  --control-vector-layer-range $LAYERS --host 127.0.0.1 --port "$PORT" > "$RUN/server.log" 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null; wait $SERVER 2>/dev/null || true' EXIT
for _ in $(seq 1 120); do
  curl -sf "http://127.0.0.1:$PORT/health" > /dev/null && break
  kill -0 $SERVER 2>/dev/null || { log "server died"; tail -20 "$RUN/server.log"; exit 3; }
  sleep 2
done
curl -sf "http://127.0.0.1:$PORT/health" > /dev/null || { log "server not healthy"; exit 3; }
log "sweep + idk"
"$PY" steering/voice_sweep.py --endpoint "http://127.0.0.1:$PORT" "${VOICE_ARGS[@]}" --scales "$SWEEP_SCALES" \
  --idk-scales "$IDK_SCALES" --parallel "$NP" --steps "$SWEEP_STEPS" --out "$RUN/$OUT" 2>&1 | tee -a "$RUN/$OUT.log"
sha256sum "$RUN"/vectors/base/*.gguf 2>/dev/null > "$RUN/vectors.sha256" || true
log "DONE"
