#!/usr/bin/env bash
# Web research v3 on the 1,180-release 2025-26 sample, then Bonsai's six books with and without it (same releases).
# v3 vs v2: the press release itself is readable (v2 blocked it in 54% of releases), the previous earnings release
# (its guidance) replaces Wikipedia, tool calls are capped at 15 s, one model round after the prefetch instead of
# two, 3 Bonsai briefs in flight instead of 1. Manual run, not a service:
#   systemd-inhibit --what=idle:sleep scripts/research_v3_run.sh [LIMIT]
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT/backend"
PY=.venv/bin/python
LIMIT=${1:-1180}
LOG() { echo "$(date '+%F %T') $*"; }
F=results/events/features_sp500_2025_secchk_research_Jan-v1-4B-GGUF_Q4_K_M_v3.csv

GPU_UTIL=0.40 nohup setsid "$ROOT/scripts/vllm_serve.sh" > results/events/vllm_v3.log 2>&1 &
VLLM=$!
OLLAMA_MODELS=$HOME/.ollama/models OLLAMA_NOPRUNE=1 OLLAMA_HOST=127.0.0.1:11435 OLLAMA_NUM_PARALLEL=3 \
  OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 \
  nohup setsid ollama serve > results/events/ollama_v3.log 2>&1 &
OLL=$!
trap 'kill -- -$VLLM 2>/dev/null; kill $OLL 2>/dev/null' EXIT
until curl -s -m 2 127.0.0.1:11435/api/tags > /dev/null; do sleep 2; done
until curl -s -m 2 127.0.0.1:8000/v1/models > /dev/null; do
  kill -0 $VLLM 2>/dev/null || { LOG "vLLM exited"; tail -20 results/events/vllm_v3.log; exit 1; }; sleep 5
done
LOG "research v3 on $LIMIT releases"
$PY scripts/research_events.py --features results/events/features_sp500_2025_secchk.csv --limit "$LIMIT" \
    --backend vllm --workers 8 || LOG "research failed"
kill -- -$VLLM 2>/dev/null
[ -f "$F" ] || { LOG "no research table"; exit 1; }
for H in 5 20 63 120 252; do
  $PY scripts/decide_events.py --events data/events/events_sp500_2025.csv --features $F --tag jan_research_v3 \
      --horizon $H --explain 0 || LOG "decide h$H failed"
done
$PY scripts/horizons6_eval.py --with-tag jan_research_v3 | tee results/events/research_v3_eval.txt
LOG done
