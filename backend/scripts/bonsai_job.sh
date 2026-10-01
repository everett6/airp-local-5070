#!/usr/bin/env bash
# Run one command with Bonsai served by Ollama on :11435, and always stop the server afterwards (research queue).
#   scripts/bonsai_job.sh .venv/bin/python scripts/llm_fields.py extract-b4
set -u
cd "$(dirname "$0")/.."
OLLAMA_MODELS=$HOME/.ollama/models OLLAMA_NOPRUNE=1 OLLAMA_HOST=127.0.0.1:11435 OLLAMA_NUM_PARALLEL=3 \
  OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 setsid ollama serve >> results/research/logs/ollama.log 2>&1 &
SRV=$!
trap 'kill -- -$SRV 2>/dev/null; kill $SRV 2>/dev/null' EXIT INT TERM
for _ in $(seq 120); do curl -s -m 2 127.0.0.1:11435/api/tags > /dev/null && break; kill -0 $SRV 2>/dev/null || exit 1; sleep 2; done
"$@"
