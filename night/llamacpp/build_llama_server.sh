#!/usr/bin/env bash
# Build llama-server from llama.cpp v0.7.0 (620d2225) with CUDA for the RTX 4090 (sm89), on the GPU host.
# Usage: night/llamacpp/build_llama_server.sh LLAMA_CPP_DIR [NVCC] [JOBS]
# Same configuration as the working Glimmer build on nelly: Release, GGML_CUDA=ON, arch 89, the host's nvcc.
# RUNPATH is $ORIGIN-relative, so build/bin can be copied to another box (same CUDA) and run from any path.
set -euo pipefail
LLAMA=$1; NVCC=${2:-/opt/cuda/bin/nvcc}; JOBS=${3:-16}
REV=620d2225cc436ab0b001e3200e590e3b286ef9ab
[ "$(git -C "$LLAMA" rev-parse HEAD)" = "$REV" ] || { echo "llama.cpp is not at $REV" >&2; exit 2; }
cmake -B "$LLAMA/build" -S "$LLAMA" -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=89 \
  -DCMAKE_CUDA_COMPILER="$NVCC" -DLLAMA_BUILD_TESTS=OFF -DLLAMA_CURL=OFF -DCMAKE_BUILD_RPATH_USE_ORIGIN=ON
cmake --build "$LLAMA/build" --config Release -j "$JOBS" --target llama-server llama-cli
"$LLAMA/build/bin/llama-server" --version
sha256sum "$LLAMA/build/bin/llama-server"
