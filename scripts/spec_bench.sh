#!/usr/bin/env bash
# Research speed benchmark: the same releases, LLM cache off, tool results replayed from the tool cache (so only the
# model side differs). Arms: base (cache-friendly prompts), nothink (no reasoning tokens in Jan's tool round), ngram
# (vLLM prompt-lookup speculative decoding). Reports seconds, prefix-cache hit rate and facts per release. Manual:
#   scripts/spec_bench.sh [N] [ARMS...]
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT/backend"
PY=.venv/bin/python
N=${1:-24}; shift || true
ARMS=${*:-base nothink ngram}
LOG() { echo "$(date '+%F %T') $*"; }
gpu_free_of_vllm() { ! nvidia-smi --query-compute-apps=process_name --format=csv,noheader | grep -qi "python\|vllm"; }
OLLAMA_MODELS=$HOME/.ollama/models OLLAMA_NOPRUNE=1 OLLAMA_HOST=127.0.0.1:11435 OLLAMA_NUM_PARALLEL=3 \
  OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 \
  nohup setsid ollama serve > results/events/ollama_bench.log 2>&1 &
OLL=$!
VLLM=""
trap '[ -n "$VLLM" ] && kill -- -$VLLM 2>/dev/null; kill $OLL 2>/dev/null' EXIT
until curl -s -m 2 127.0.0.1:11435/api/tags > /dev/null; do sleep 2; done
for ARM in $ARMS; do
  until gpu_free_of_vllm; do sleep 3; done  # the previous arm's engine must have released its memory
  D="results/events_research_Jan-v1-4B-GGUF_Q4_K_M_bench_$ARM"; rm -rf "$D"
  EXTRA=(); [ "$ARM" = nothink ] && EXTRA=(--no-jan-thinking)
  SPEC=$([ "$ARM" = ngram ] && echo ngram || echo "") GPU_UTIL=0.40 \
    nohup setsid "$ROOT/scripts/vllm_serve.sh" > results/events/vllm_bench_$ARM.log 2>&1 &
  VLLM=$!
  until curl -s -m 2 127.0.0.1:8000/v1/models > /dev/null; do
    kill -0 $VLLM 2>/dev/null || { LOG "$ARM: vLLM exited: $(grep -m1 -o 'ValueError.*' results/events/vllm_bench_$ARM.log)"; VLLM=""; continue 2; }
    sleep 5
  done
  T0=$(date +%s)
  $PY scripts/research_events.py --features results/events/features_sp500_2025_secchk.csv --limit "$N" \
      --backend vllm --workers 8 --no-llm-cache --run-tag "_bench_$ARM" "${EXTRA[@]}" > results/events/spec_bench_$ARM.log 2>&1
  T=$(( $(date +%s) - T0 ))
  HIT=$(grep -o "Prefix cache hit rate: [0-9.]*%" results/events/vllm_bench_$ARM.log | tail -1)
  ACC=$(grep -o "Avg Draft acceptance rate: [0-9.]*%" results/events/vllm_bench_$ARM.log | tail -1)
  FACTS=$($PY -c "import json,glob; f=glob.glob('$D/*.json'); print(round(sum(len((json.load(open(x)).get('brief') or {}).get('facts',[])) for x in f)/max(1,len(f)),2))")
  LOG "$ARM: $T s for $N releases ($(( T / N )) s each); facts/release $FACTS; $HIT ${ACC:+; $ACC}"
  kill -- -$VLLM 2>/dev/null; wait $VLLM 2>/dev/null; VLLM=""
done
LOG done
