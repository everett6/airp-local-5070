#!/usr/bin/env bash
# "LLM extracts, code scores", arms A and B (docs/PLAN_60_V2.md): Jan gathers as-of research for the 2024 sample
# (vLLM, GPU to itself); Bonsai labels release + research (arm B), then finishes the release-only labels (arm A,
# resumes where it stopped); code scores arm A, then arm B. Jan runs first (27 Sep, user's request). One model on the GPU at a time. Resumable. Manual run, not a service:
#   systemd-inhibit --what=idle:sleep scripts/jan_bonsai_fields_run.sh
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT/backend"
PY=.venv/bin/python
LOG() { echo "$(date '+%F %T') $*"; }
VLLM=""; OLL=""
trap '[ -n "$VLLM" ] && kill -- -$VLLM 2>/dev/null; [ -n "$OLL" ] && kill $OLL 2>/dev/null' EXIT
gpu_idle() { until ! nvidia-smi --query-compute-apps=process_name --format=csv,noheader | grep -qi "python\|vllm\|ollama"; do sleep 5; done; }

pkill -f "[o]llama serve" ; sleep 5; gpu_idle

LOG "Jan: as-of research on the 2024 sample"
MAX_SEQS=${MAX_SEQS:-16} GPU_UTIL=${GPU_UTIL:-0.85} nohup setsid "$ROOT/scripts/vllm_serve.sh" > results/events/vllm_2024.log 2>&1 &
VLLM=$!
until curl -s -m 2 127.0.0.1:8000/v1/models > /dev/null; do
  kill -0 $VLLM 2>/dev/null || { LOG "vLLM exited"; tail -20 results/events/vllm_2024.log; exit 1; }; sleep 5
done
$PY scripts/research_events.py --features results/events/features_sp500_2024_secchk.csv \
  --events data/events/events_sp500_2024.csv --run-tag _v3_2024 --backend vllm --workers 16 --phase gather \
  || LOG "gather failed"
kill -- -$VLLM 2>/dev/null; wait $VLLM 2>/dev/null; VLLM=""; gpu_idle
$PY scripts/research_audit.py results/events_research_Jan-v1-4B-GGUF_Q4_K_M_v3_2024 | tail -3

LOG "Bonsai: labels from release + research"
OLLAMA_MODELS=$HOME/.ollama/models OLLAMA_NOPRUNE=1 OLLAMA_HOST=127.0.0.1:11435 OLLAMA_NUM_PARALLEL=3 \
  OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 \
  nohup setsid ollama serve > results/events/ollama_llm_fields_r.log 2>&1 &
OLL=$!
until curl -s -m 2 127.0.0.1:11435/api/tags > /dev/null; do sleep 2; done
$PY scripts/llm_fields.py extract-research || LOG "arm B labels failed"
LOG "Bonsai: finish release-only labels (arm A)"
$PY scripts/llm_fields.py extract --prompt P2 || LOG "arm A labels failed"
kill $OLL 2>/dev/null; OLL=""

LOG "code scores arm A, then arm B (pre-registered)"
$PY scripts/llm_fields.py test 2>&1 | tee results/events/llm_fields_test.txt
$PY scripts/llm_fields.py test-research 2>&1 | tee results/events/llm_fields_research_test.txt
LOG done
