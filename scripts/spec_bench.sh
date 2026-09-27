#!/usr/bin/env bash
# Does speculative decoding speed up web research? Same releases, LLM cache off, tool results replayed from the tool
# cache in both arms (so only the models differ): Jan on vLLM without and with n-gram (prompt-lookup) speculation.
# Run after a research run has cached the tools for these releases. Manual:
#   scripts/spec_bench.sh [N]
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT/backend"
PY=.venv/bin/python
N=${1:-24}
LOG() { echo "$(date '+%F %T') $*"; }
OLLAMA_MODELS=$HOME/.ollama/models OLLAMA_NOPRUNE=1 OLLAMA_HOST=127.0.0.1:11435 OLLAMA_NUM_PARALLEL=3 \
  OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 \
  nohup setsid ollama serve > results/events/ollama_bench.log 2>&1 &
OLL=$!
VLLM=""
trap '[ -n "$VLLM" ] && kill -- -$VLLM 2>/dev/null; kill $OLL 2>/dev/null' EXIT
until curl -s -m 2 127.0.0.1:11435/api/tags > /dev/null; do sleep 2; done
for ARM in off ngram; do
  rm -rf "results/events_research_Jan-v1-4B-GGUF_Q4_K_M_bench_$ARM"
  SPEC=$([ $ARM = ngram ] && echo ngram || echo "") GPU_UTIL=0.40 \
    nohup setsid "$ROOT/scripts/vllm_serve.sh" > results/events/vllm_bench_$ARM.log 2>&1 &
  VLLM=$!
  until curl -s -m 2 127.0.0.1:8000/v1/models > /dev/null; do
    kill -0 $VLLM 2>/dev/null || { LOG "vLLM ($ARM) exited"; tail -20 results/events/vllm_bench_$ARM.log; exit 1; }; sleep 5
  done
  T0=$(date +%s)
  $PY scripts/research_events.py --features results/events/features_sp500_2025_secchk.csv --limit "$N" \
      --backend vllm --workers 8 --no-llm-cache --run-tag "_bench_$ARM" > results/events/spec_bench_$ARM.log 2>&1
  LOG "$ARM: $(( $(date +%s) - T0 )) s for $N releases; $(tail -1 results/events/spec_bench_$ARM.log)"
  grep -iE "acceptance|accepted" results/events/vllm_bench_$ARM.log | tail -2
  kill -- -$VLLM 2>/dev/null; wait $VLLM 2>/dev/null; VLLM=""
  until ! curl -s -m 2 127.0.0.1:8000/v1/models > /dev/null; do sleep 2; done
done
LOG done
