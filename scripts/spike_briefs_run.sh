#!/usr/bin/env bash
# Spike check cause briefs for every historical trigger: Jan gathers on vLLM, then Bonsai labels on Ollama, each with
# the GPU to itself (same pattern as scripts/research_v3_run.sh). Manual run, not a service:
#   systemd-inhibit --what=idle:sleep scripts/spike_briefs_run.sh
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT/backend"
PY=.venv/bin/python
LOG() { echo "$(date '+%F %T') $*"; }
VLLM=""; OLL=""
trap '[ -n "$VLLM" ] && kill -- -$VLLM 2>/dev/null; [ -n "$OLL" ] && kill $OLL 2>/dev/null' EXIT
LOG "gather (Jan)"
MAX_SEQS=${MAX_SEQS:-16} GPU_UTIL=${GPU_UTIL:-0.85} nohup setsid "$ROOT/scripts/vllm_serve.sh" > results/spike_vllm.log 2>&1 &
VLLM=$!
until curl -s -m 2 127.0.0.1:8000/v1/models > /dev/null; do
  kill -0 $VLLM 2>/dev/null || { LOG "vLLM exited"; tail -20 results/spike_vllm.log; exit 1; }; sleep 5
done
$PY scripts/spike_cause_briefs.py --phase gather || LOG "gather failed"
kill -- -$VLLM 2>/dev/null; wait $VLLM 2>/dev/null; VLLM=""
until ! nvidia-smi --query-compute-apps=process_name --format=csv,noheader | grep -qi "python\|vllm"; do sleep 3; done
LOG "label (Bonsai)"
OLLAMA_MODELS=$HOME/.ollama/models OLLAMA_NOPRUNE=1 OLLAMA_HOST=127.0.0.1:11435 OLLAMA_NUM_PARALLEL=3 \
  OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 \
  nohup setsid ollama serve > results/spike_ollama.log 2>&1 &
OLL=$!
until curl -s -m 2 127.0.0.1:11435/api/tags > /dev/null; do sleep 2; done
$PY scripts/spike_cause_briefs.py --phase label || LOG "label failed"
LOG done
