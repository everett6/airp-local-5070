#!/usr/bin/env bash
# Arm B resume (docs/PLAN_60_V2.md "Context overflow", fixed 2026-09-27 before resuming): arm B's labels stopped at
# 960/1995 (2024) when one release + its research exceeded Bonsai's 8,192-token window. llm_fields.py now records such
# a release as unparsed (defaults) instead of stopping. Waits for the 8-K run (one model on the GPU at a time), then:
# arm B labels (resumes; the 960 done are kept) -> arm B test -> arm C (bonsai_judgement_run.sh). Resumable.
# Manual run, not a service:  systemd-inhibit --what=idle:sleep scripts/arm_b_resume_run.sh
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT/backend"
PY=.venv/bin/python
LOG() { echo "$(date '+%F %T') $*"; }
OLL=""
trap '[ -n "$OLL" ] && kill $OLL 2>/dev/null' EXIT
gpu_idle() { until ! nvidia-smi --query-compute-apps=process_name --format=csv,noheader | grep -qi "python\|vllm\|ollama"; do sleep 5; done; }

LOG "waiting for the 8-K run to finish"
while pgrep -f "[n]ews8k_run.sh" > /dev/null; do sleep 60; done
gpu_idle
LOG "Bonsai: arm B labels (resume)"
OLLAMA_MODELS=$HOME/.ollama/models OLLAMA_NOPRUNE=1 OLLAMA_HOST=127.0.0.1:11435 OLLAMA_NUM_PARALLEL=3 \
  OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 \
  nohup setsid ollama serve > results/events/ollama_llm_fields_r2.log 2>&1 &
OLL=$!
until curl -s -m 2 127.0.0.1:11435/api/tags > /dev/null; do sleep 2; done
if ! $PY scripts/llm_fields.py extract-research; then LOG "arm B labels failed"; exit 1; fi
kill $OLL 2>/dev/null; OLL=""; sleep 5
LOG "code scores arm B (pre-registered)"
$PY scripts/llm_fields.py test-research 2>&1 | tee results/events/llm_fields_research_test.txt
LOG "arm B done; arm C next"
"$ROOT/scripts/bonsai_judgement_run.sh" 2>&1 | tee -a results/events/bonsai_judgement_run.log
LOG done
