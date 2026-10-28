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
