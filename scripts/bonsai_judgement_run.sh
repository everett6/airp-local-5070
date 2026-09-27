#!/usr/bin/env bash
# Arm C (docs/PLAN_60_V2.md "Arm C"): Bonsai with judgement fields. Waits for jan_bonsai_fields_run.sh to finish (one
# model on the GPU at a time), then: quality gate on 100 dev releases -> full extraction -> the pre-registered test.
# Resumable. Manual run, not a service:
#   systemd-inhibit --what=idle:sleep scripts/bonsai_judgement_run.sh
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT/backend"
PY=.venv/bin/python
LOG() { echo "$(date '+%F %T') $*"; }
OLL=""
trap '[ -n "$OLL" ] && kill $OLL 2>/dev/null' EXIT
gpu_idle() { until ! nvidia-smi --query-compute-apps=process_name --format=csv,noheader | grep -qi "python\|vllm\|ollama"; do sleep 5; done; }

LOG "waiting for the Jan -> Bonsai run to finish"
while pgrep -f "[j]an_bonsai_fields_run.sh" > /dev/null; do sleep 60; done
pkill -f "[o]llama serve"; sleep 5; gpu_idle

LOG "Bonsai (judgement): quality gate"
OLLAMA_MODELS=$HOME/.ollama/models OLLAMA_NOPRUNE=1 OLLAMA_HOST=127.0.0.1:11435 OLLAMA_NUM_PARALLEL=3 \
  OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 \
  nohup setsid ollama serve > results/events/ollama_judgement.log 2>&1 &
OLL=$!
until curl -s -m 2 127.0.0.1:11435/api/tags > /dev/null; do sleep 2; done
if ! $PY scripts/llm_fields.py dev-judgement; then
  LOG "quality gate FAILED: nothing extracted (see results/events/llm_fields_judgement_dev.json)"; exit 3
fi
LOG "Bonsai (judgement): labels on 2024 + 2025-26"
$PY scripts/llm_fields.py extract-judgement || LOG "arm C labels failed"
kill $OLL 2>/dev/null; OLL=""

LOG "code scores arm C (pre-registered)"
$PY scripts/llm_fields.py test-judgement 2>&1 | tee results/events/llm_fields_judgement_test.txt
LOG done
