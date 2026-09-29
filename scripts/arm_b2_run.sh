#!/usr/bin/env bash
# Arm B2 (docs/PLAN_60_V2.md "Arm B2", spec fixed before any B2 data): waits for the news warm-up
# (warm_news.py) and for the arm B/C chain (one model on the GPU at a time); applies the stop rule (2024 news coverage
# >= 50%); then Jan re-gathers both samples with arm B's settings -> audit -> Bonsai labels (PROMPT_R) -> the test.
# Resumable. Manual run, not a service:  systemd-inhibit --what=idle:sleep scripts/arm_b2_run.sh
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT/backend"
PY=.venv/bin/python
LOG() { echo "$(date '+%F %T') $*"; }
VLLM=""; OLL=""
trap '[ -n "$VLLM" ] && kill -- -$VLLM 2>/dev/null; [ -n "$OLL" ] && kill $OLL 2>/dev/null' EXIT
gpu_idle() { until ! nvidia-smi --query-compute-apps=process_name --format=csv,noheader | grep -qi "python\|vllm\|ollama"; do sleep 5; done; }

LOG "waiting for the news warm-up and the arm B/C chain"
while pgrep -f "[w]arm_news.py|[a]rm_b_resume_run.sh|[b]onsai_judgement_run.sh" > /dev/null; do sleep 60; done
COV=$($PY -c "
import json; r=[json.loads(x) for x in open('results/events/warm_news.jsonl') if '\"2024\"' in x]
print(round(sum(x['status']!='none' for x in r)/max(1,len(r)),3))")
LOG "2024 news coverage after warm-up: $COV"
if $PY -c "import sys; sys.exit(0 if $COV >= 0.5 else 1)"; then :; else
  LOG "STOP RULE: coverage below 50%; B2 not run (spec: a different free news source instead)"; exit 3; fi
gpu_idle
for S in 2024 2025; do
  if [ $S = 2024 ]; then F=features_sp500_2024_secchk; E=events_sp500_2024; T=_v3b2_2024; else F=features_sp500_2025_secchk; E=events_sp500_2025; T=_v3b2; fi
  LOG "Jan: as-of research, $S sample ($T)"
  MAX_SEQS=${MAX_SEQS:-16} GPU_UTIL=${GPU_UTIL:-0.85} nohup setsid "$ROOT/scripts/vllm_serve.sh" > results/events/vllm_b2.log 2>&1 &
  VLLM=$!
  until curl -s -m 2 127.0.0.1:8000/v1/models > /dev/null; do
    kill -0 $VLLM 2>/dev/null || { LOG "vLLM exited"; tail -20 results/events/vllm_b2.log; exit 1; }; sleep 5
  done
  $PY scripts/research_events.py --features results/events/$F.csv --events data/events/$E.csv --run-tag $T \
    --backend vllm --workers 16 --phase gather || LOG "gather failed ($S)"
  kill -- -$VLLM 2>/dev/null; wait $VLLM 2>/dev/null; VLLM=""; gpu_idle
  $PY scripts/research_audit.py results/events_research_Jan-v1-4B-GGUF_Q4_K_M$T | tail -3
done
LOG "Bonsai: arm B2 labels"
OLLAMA_MODELS=$HOME/.ollama/models OLLAMA_NOPRUNE=1 OLLAMA_HOST=127.0.0.1:11435 OLLAMA_NUM_PARALLEL=3 \
  OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 \
  nohup setsid ollama serve > results/events/ollama_b2.log 2>&1 &
OLL=$!
until curl -s -m 2 127.0.0.1:11435/api/tags > /dev/null; do sleep 2; done
if ! $PY scripts/llm_fields.py extract-research-b2; then LOG "arm B2 labels failed"; exit 1; fi
kill $OLL 2>/dev/null; OLL=""
LOG "code scores arm B2 (pre-registered)"
$PY scripts/llm_fields.py test-research-b2 2>&1 | tee results/events/llm_fields_research_b2_test.txt
LOG done
