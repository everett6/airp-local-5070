#!/usr/bin/env bash
# Serve Jan-v1-4B with vLLM for the research agent (backend/scripts/research_events.py --backend vllm).
# Run by hand; not a service. Stop it with Ctrl+C (or kill the PID it prints). Bonsai stays on Ollama next to it.
#
#   scripts/vllm_serve.sh              # NVFP4 checkpoint (backend/scripts/quantize_nvfp4.py): fits next to Bonsai
#   QUANT=fp8 scripts/vllm_serve.sh    # FP8 quantized on load: ~4.5 GB of weights, only fits with the GPU to itself
#
# Needs: ~/vllm-env (Python 3.12 + vllm), the Jan weights in ~/models/Jan-v1-4B (hf download janhq/Jan-v1-4B).
# Memory: GPU_UTIL is vLLM's share of the 12 GB card (weights + KV cache); Bonsai on Ollama needs ~5 GB of the rest.
set -euo pipefail
QUANT=${QUANT:-nvfp4}
GPU_UTIL=${GPU_UTIL:-0.40}  # Bonsai (3 slots x 8k context) must fit beside it: 0.50 and 0.45 pushed it partly onto the CPU
if [ "$QUANT" = "nvfp4" ]; then
  MODEL=$HOME/models/Jan-v1-4B-NVFP4; QARGS=(--kv-cache-dtype fp8)
else
  MODEL=$HOME/models/Jan-v1-4B; QARGS=(--quantization fp8)
fi
# FlashInfer attention/sampling compiles kernels at first use and needs the CUDA toolkit's nvcc; without it, Triton
if [ -x /usr/local/cuda/bin/nvcc ]; then
  # MAX_JOBS: the first start compiles kernels; unlimited parallel nvcc jobs ran the 29 GB of RAM out (2026-09-25 crash)
  export CUDA_HOME=/usr/local/cuda PATH=/usr/local/cuda/bin:$PATH MAX_JOBS=${MAX_JOBS:-2} FLASHINFER_NVCC_THREADS=1
  ATTN=(--attention-backend FLASHINFER)
else
  export VLLM_USE_FLASHINFER_SAMPLER=0
  ATTN=(--attention-backend TRITON_ATTN)
fi
exec "$HOME/vllm-env/bin/vllm" serve "$MODEL" --served-model-name jan-v1-4b --host 127.0.0.1 --port 8000 \
  --max-model-len 8192 --gpu-memory-utilization "$GPU_UTIL" --enable-prefix-caching --max-num-seqs 8 \
  --max-num-batched-tokens 4096 \
  --enable-auto-tool-choice --tool-call-parser hermes --reasoning-parser qwen3 "${ATTN[@]}" \
  "${QARGS[@]}"
