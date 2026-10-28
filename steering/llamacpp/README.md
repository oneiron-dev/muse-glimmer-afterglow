# Per-request control vectors for llama-server

`per-request-control-vectors.patch` applies to llama.cpp v0.7.0 (`620d2225`). It lets each request set the
strength of the control vectors the server loaded at start, the same way it already sets LoRA scales.

Stock llama-server takes a control vector only at load (`--control-vector-scaled FILE:SCALE`), so changing a dial
means a restart. With the patch:

- Start the server with every dial's GGUF loaded at scale 0:
  `--control-vector-scaled concise.gguf:0,formal.gguf:0,...` (optionally `--control-vector-layer-range 16 42`).
  A vector's `id` is its position in that list.
- A request names the scales it wants: `"control_vectors": [{"id": 0, "scale": 0.04}]`. Vectors it does not list
  are at 0. A request without the field keeps every vector at its load-time scale, so a server started with all
  vectors at 0 answers exactly as stock until a request asks for a dial.
- A changed scale clears the slot's prompt cache, as a LoRA change does. Slots with different scales are never in
  one batch, and the scales go into the target context once per batch, just before decode (next to the LoRA
  scales). The drafter context never gets a control vector.
- A saved KV snapshot holds the scales it was computed with. Restore it only for requests at those scales.

What changes in the code: a `control_vectors` request field, a per-slot `cvec_scales`, one file's data kept per
loaded vector at strength 1, and `apply_control_vectors()`, which sums the vectors at the batch's scales and calls
`llama_set_adapter_cvec` (or clears the vector when every scale is 0). With no control vector loaded, none of
the new code runs.

## Build and gate

```bash
git -C LLAMA_CPP apply steering/llamacpp/per-request-control-vectors.patch
night/llamacpp/build_llama_server.sh LLAMA_CPP /opt/cuda/bin/nvcc 8
python3 steering/llamacpp/test_control_vectors.py --stock STOCK/llama-server --patched LLAMA_CPP/build/bin/llama-server \
  --model tiny.gguf --lora tiny-lora.gguf --vec-a a.gguf --vec-b b.gguf
```

The test compares greedy completions from a stock and a patched server and requires byte-equal answers in five
cases: nothing loaded (LoRA off and on); a load-time scale; a per-request scale against stock loaded statically at
that scale (LoRA off and on); switching scales on one slot with the prompt cache on; and two vectors combined.
