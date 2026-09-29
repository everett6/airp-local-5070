#!/usr/bin/env bash
# Arm B3 (docs/PLAN_60_V2.md "Arm B3", spec fixed before arm B2's verdict): waits for the evidence builder
# (b3_evidence.py) and for arm B2 (one model on the GPU at a time); then Bonsai labels (PROMPT_R) -> the test.
# Resumable. Manual run, not a service:  systemd-inhibit --what=idle:sleep scripts/arm_b3_run.sh
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT/backend"
PY=.venv/bin/python
LOG() { echo "$(date '+%F %T') $*"; }
OLL=""
trap '[ -n "$OLL" ] && kill $OLL 2>/dev/null' EXIT
gpu_idle() { until ! nvidia-smi --query-compute-apps=process_name --format=csv,noheader | grep -qi "python\|vllm\|ollama"; do sleep 5; done; }

LOG "waiting for the B3 evidence builder and arm B2"
while pgrep -f "[b]3_evidence.py|[a]rm_b2_run.sh" > /dev/null; do sleep 60; done
[ -f results/events/b3_evidence_stats.json ] || { LOG "no evidence stats: the builder failed"; exit 1; }
cat results/events/b3_evidence_stats.json
gpu_idle
LOG "Bonsai: arm B3 labels"
OLLAMA_MODELS=$HOME/.ollama/models OLLAMA_NOPRUNE=1 OLLAMA_HOST=127.0.0.1:11435 OLLAMA_NUM_PARALLEL=3 \
  OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 \
  nohup setsid ollama serve > results/events/ollama_b3.log 2>&1 &
OLL=$!
until curl -s -m 2 127.0.0.1:11435/api/tags > /dev/null; do sleep 2; done
if ! $PY scripts/llm_fields.py extract-research-b3; then LOG "arm B3 labels failed"; exit 1; fi
kill $OLL 2>/dev/null; OLL=""
LOG "code scores arm B3 (pre-registered)"
$PY scripts/llm_fields.py test-research-b3 2>&1 | tee results/events/llm_fields_research_b3_test.txt
LOG done
