#!/usr/bin/env bash
# Web research v3 on the 1,180-release 2025-26 sample, then Bonsai's books with and without it (same releases).
# Two phases, each with the GPU to itself (sharing it made each Bonsai brief 5-6x slower):
#   1. gather: Jan-v1-4B on vLLM + the as-of tools; the evidence is saved per release. vLLM is then stopped.
#   2. brief:  Bonsai-27B on Ollama writes the source-checked brief from the saved evidence.
# Then Bonsai decides each book on the researched fact sheets. Every step is resumable. Manual run, not a service:
#   systemd-inhibit --what=idle:sleep scripts/research_v3_run.sh [LIMIT]
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT/backend"
PY=.venv/bin/python
LIMIT=${1:-1180}
LOG() { echo "$(date '+%F %T') $*"; }
F=results/events/features_sp500_2025_secchk_research_Jan-v1-4B-GGUF_Q4_K_M_v3.csv
ARGS=(--features results/events/features_sp500_2025_secchk.csv --limit "$LIMIT")
VLLM=""; OLL=""
trap '[ -n "$VLLM" ] && kill -- -$VLLM 2>/dev/null; [ -n "$OLL" ] && kill $OLL 2>/dev/null' EXIT

LOG "phase 1: gather (Jan + tools) on $LIMIT releases"
GPU_UTIL=${GPU_UTIL:-0.85} nohup setsid "$ROOT/scripts/vllm_serve.sh" > results/events/vllm_v3.log 2>&1 &
VLLM=$!
until curl -s -m 2 127.0.0.1:8000/v1/models > /dev/null; do
  kill -0 $VLLM 2>/dev/null || { LOG "vLLM exited"; tail -20 results/events/vllm_v3.log; exit 1; }; sleep 5
done
$PY scripts/research_events.py "${ARGS[@]}" --backend vllm --workers 12 --phase gather || LOG "gather failed"
kill -- -$VLLM 2>/dev/null; wait $VLLM 2>/dev/null; VLLM=""
until ! nvidia-smi --query-compute-apps=process_name --format=csv,noheader | grep -qi "python\|vllm"; do sleep 3; done

LOG "phase 2: briefs (Bonsai)"
OLLAMA_MODELS=$HOME/.ollama/models OLLAMA_NOPRUNE=1 OLLAMA_HOST=127.0.0.1:11435 OLLAMA_NUM_PARALLEL=3 \
  OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 \
  nohup setsid ollama serve > results/events/ollama_v3.log 2>&1 &
OLL=$!
until curl -s -m 2 127.0.0.1:11435/api/tags > /dev/null; do sleep 2; done
$PY scripts/research_events.py "${ARGS[@]}" --phase brief --brief-workers 3 || LOG "briefs failed"
[ -f "$F" ] || { LOG "no research table"; exit 1; }

LOG "decisions"
for H in 5 20 63 120 252; do
  $PY scripts/decide_events.py --events data/events/events_sp500_2025.csv --features $F --tag jan_research_v3 \
      --horizon $H --explain 0 || LOG "decide h$H failed"
done
$PY scripts/horizons6_eval.py --with-tag jan_research_v3 | tee results/events/research_v3_eval.txt
LOG done
