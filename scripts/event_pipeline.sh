#!/usr/bin/env bash
# Earnings-event pipeline (docs/MASTER_PLAN.md steps 1-5), one GPU model at a time. Not a service: run it by hand;
# every step is resumable (finished work is skipped), so re-running after a stop just continues.
#
#   scripts/event_pipeline.sh            # from the repo root
#
# Ollama: qwen3:8b from the system model store on :11434; Bonsai models from ~/.ollama on :11435. Each GPU job takes
# the GPU lock and unloads its model when done, so the two servers never hold models on the GPU at the same time.
set -euo pipefail
cd "$(dirname "$0")/../backend"
PY=.venv/bin/python
EVENTS=data/events/events_2024-01-01_2026-09-24.csv
PRICES=data/events/ohlcv_2023-01-01_2026-09-25.parquet
LOG() { echo "[$(date '+%F %T')] $*"; }

serve() {  # port models_dir
  if ! curl -s "127.0.0.1:$1/api/tags" >/dev/null; then
    OLLAMA_MODELS="$2" OLLAMA_NOPRUNE=1 OLLAMA_HOST="127.0.0.1:$1" OLLAMA_NUM_PARALLEL=1 OLLAMA_MAX_LOADED_MODELS=1 \
      nohup ollama serve >"/tmp/ollama_$1.log" 2>&1 &
    for _ in $(seq 30); do curl -s "127.0.0.1:$1/api/tags" >/dev/null && break; sleep 1; done
  fi
}
serve 11434 /usr/share/ollama/.ollama/models
serve 11435 "$HOME/.ollama/models"

LOG "waiting for the event list and prices"
until [ -f "$EVENTS" ] && [ -f "$PRICES" ]; do sleep 20; done

LOG "step 2b: download press releases (network only)"
$PY scripts/extract_events.py fetch --events "$EVENTS" --from 2025-01-01

LOG "code-only baselines"
$PY scripts/event_eval.py --events "$EVENTS" --prices "$PRICES" --tag baseline | tee results/events/eval_baseline.txt

LOG "import the Bonsai models into the :11435 store (no GPU)"
B1=/home/everett/.lmstudio/models/lmstudio-community/Bonsai-27B-GGUF/Bonsai-27B-Q1_0.gguf
B2=/home/everett/.lmstudio/models/prism-ml/Ternary-Bonsai-2-27B-gguf/Ternary-Bonsai-2-27B-PTQ1_0.gguf
MODELS=(bonsai-27b:latest)
if [ -f "$B2" ]; then
  printf 'FROM %s\n' "$B2" >/tmp/Modelfile.bonsai2
  if OLLAMA_HOST=127.0.0.1:11435 ollama create bonsai2-27b -f /tmp/Modelfile.bonsai2 >/dev/null 2>&1 && \
     $PY -c "
import json, urllib.request
from app.sandbox.gpu_lock import gpu_job
body = {'model': 'bonsai2-27b', 'stream': False, 'think': False, 'messages': [{'role': 'user', 'content': 'Say OK'}],
        'options': {'num_predict': 3}, 'keep_alive': 0}
with gpu_job('bonsai2 load test'):  # never alongside another GPU job
    r = urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:11435/api/chat', json.dumps(body).encode(),
                               {'Content-Type': 'application/json'}), timeout=300)
    print(json.load(r)['message']['content'])" ; then
    MODELS+=(bonsai2-27b:latest)
    LOG "Ternary-Bonsai-2 loads in Ollama: it gets the same tests"
  else
    LOG "Ternary-Bonsai-2 does not load in this Ollama (quant type PTQ1_0); skipped, recorded here"
  fi
fi

LOG "step 1: head-to-head on the 400 research briefs"
until [ "$(ls results/analyst/*/*.json 2>/dev/null | wc -l)" -ge 400 ]; do sleep 60; done
for M in "${MODELS[@]}"; do
  $PY scripts/decide.py --model "$M" --base-url http://127.0.0.1:11435 --briefs analyst
  $PY scripts/llm_web_report.py --run "decisions_${M//:/_}" >/dev/null
done

LOG "step 3: reader (qwen3:8b) on every clean-window release"
$PY scripts/extract_events.py extract --events "$EVENTS" --from 2025-01-01 --model qwen3:8b

LOG "step 5: decisions on every event"
for M in "${MODELS[@]}"; do
  $PY scripts/decide_events.py --model "$M" --base-url http://127.0.0.1:11435 --events "$EVENTS" --prices "$PRICES"
  $PY scripts/event_eval.py --events "$EVENTS" --prices "$PRICES" \
      --extract results/events/extract_qwen3_8b.jsonl --decide "results/events/decide_${M//:/_}.jsonl" \
      --tag "${M//:/_}" | tee "results/events/eval_${M//:/_}.txt"
done
LOG "pipeline done"
