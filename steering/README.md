# Steering: control vectors for Glimmer with re:dvx

A control vector is a direction in Glimmer's residual stream. Adding it to each layer's output pushes replies
toward one pole of an axis: "admits it does not know" against "always answers", or concise against detailed. The
base weights and the night's LoRA adapter stay unchanged. The vector is added at serving time, at a strength the
caller picks.

[re:dvx](https://github.com/lumpenspace/redvx) (MIT, `redvx` 0.1.0 from PyPI) supplies the protocol: contrast pairs
that differ only in a persona sentence, one shared supplied answer token, centered PCA on the paired differences,
common-component removal across axes, and the vector format. Glimmer needs transformers 5.15. re:dvx pins
`transformers<5.9`, so it is installed with `pip install --no-deps redvx==0.1.0` into a venv that reuses the night
venv's packages. Every re:dvx module imports under 5.15.

## Files

| File | What it does |
|---|---|
| `axes.json` | Five axes: abstention, concise, formal, decisive, asks-first. Each has persona templates, pole phrases and extraction prompts, and the day axes also have held-out test prompts. It also holds the check probes. Synthetic prompts only. |
| `extract_states.py` | GPU job (transformers, NF4). Reads each decoder layer's output at the supplied token for every pair, on the base model and on each PEFT adapter. Also reads the check probes and the eval prompt sets at the answer header. With `--calibrate-scales` it runs a short greedy strength sweep. |
| `build_prompt_sets.py` | Writes the eval prompt sets (gate slice, bare gate questions, anchor pool, never-told, forward probes) in the night eval's prompt form. |
| `fit_vectors.py` | CPU. Fits the re:dvx directions and writes re:dvx `WriteVector` JSON plus llama.cpp control-vector GGUFs, and the behaviour check on the check probes. |
| `read_axes.py` | CPU. Reads every axis on the eval prompt sets, base against adapter, and gives the abstention AUC against the model's own abstentions. |
| `screen.py` | GPU job (llama-server). Runs one server per vector and scale, answers every test with the adapter off and on, and scores with the night eval's harness. |
| `make_plan.py` | Writes the screen's abstention plan and day-axis plan. |

## The llama.cpp link

re:dvx steers with a forward hook that adds `coeff × vector[L]` to decoder layer L's output at every position.
llama.cpp adds a control vector at the same point: `build_cvec` runs after the FFN residual add in the Glimmer
graph. So a GGUF tensor `direction.L` holds the same vector. `fit_vectors.py` stores each layer's unit direction
times that layer's mean residual norm, so `--control-vector-scaled FILE:s` adds s × the residual norm. The steering
layers follow re:dvx's `scaled_repeng` mask, which for Glimmer's 52 layers gives 16-42 and 51.

## Voices

A voice is how Afterglow's replies sound. The user picks one on the day page; it applies from the next message.

- **Presets** (`voices.json`): named bundles of the served dials (concise, formal, decisive, at their tested
  scales, either end where that end is safe). asks-first and abstention are measured, never dials.
- **Custom voice**: the user describes a voice in one sentence. That night:
  1. `voice_pairs.py` turns the sentence into a re:dvx contrast set (`voices/<slug>/axis.json`): pole sentences
     that finish "You are an assistant that ...", everyday extraction prompts and held-out test prompts.
     Sentences about health, diet, fitness or mood, or asking for a voice that guesses, are refused.
  2. `voice_job.sh` (GPU, one card, ~30 min for three voices): extract states on the NF4 base for the five
     standard axes and every voice; fit the vectors with re:dvx (`fit_vectors.py`); run the behaviour check with
     each voice's vector added (`behaviour_check.py --vector`, from the served GGUF, layers 16-42); start the
     per-request control-vector llama-server and run `voice_sweep.py`: test-prompt replies at strengths 0 to 0.08
     and the "I don't know" rate on the 200 gate questions at each strength. Presets get the same two reads.
  3. `voice_report.py` (CPU): Muse Spark judges each steered reply against the unsteered one (sounds more like the
     voice? coherent?), then three rules fixed before the run decide which strengths ship: heard (judged toward
     the voice on >= 75% of test prompts), coherent (>= 4.5 of 5, no worse than unsteered - 0.5, no empty reply)
     and honest (on the 200 questions it says "I don't know" at least as often as without the voice, minus 4,
     and keeps all but 3 exact-right answers). A voice that makes the model guess more does not ship. Writes
     `voices/<slug>/voice.json` (status, vector, strength range, the rule, the "I don't know" counts, the
     behaviour check) and the evidence under `eval/results/2026-11-04-voice/`.
  4. `propose_voice.py` adds the ready voice to the night's `steering-proposals.json`; the morning report offers
     it like any dial change. Accepted, it is a dial from the next day (`app/afterglow/dials.py`), served once the
     control-vector build loads the voice's GGUF.
