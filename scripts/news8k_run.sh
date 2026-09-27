#!/usr/bin/env bash
# 8-K watcher W1 (docs/PLAN_60_V2.md "8-K breaking-news watcher"): waits for the judgement run (one model on the GPU
# at a time), then: quality gate on 100 filings -> labels for all -> the pre-registered test. Resumable.
# Manual run, not a service:  systemd-inhibit --what=idle:sleep scripts/news8k_run.sh
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT/backend"
PY=.venv/bin/python
LOG() { echo "$(date '+%F %T') $*"; }
OLL=""
trap '[ -n "$OLL" ] && kill $OLL 2>/dev/null' EXIT
LOG "waiting for the earlier GPU runs to finish"
while pgrep -f "[j]an_bonsai_fields_run.sh|[b]onsai_judgement_run.sh" > /dev/null; do sleep 60; done
LOG "texts"
$PY scripts/news8k.py fetch || { LOG "fetch failed"; exit 1; }
LOG "Bonsai (8-K): quality gate"
OLLAMA_MODELS=$HOME/.ollama/models OLLAMA_NOPRUNE=1 OLLAMA_HOST=127.0.0.1:11438 OLLAMA_NUM_PARALLEL=3 \
  OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 \
  nohup setsid ollama serve > results/events/ollama_news8k.log 2>&1 &
OLL=$!
until curl -s -m 2 127.0.0.1:11438/api/tags > /dev/null; do sleep 2; done
if ! $PY scripts/news8k.py dev; then LOG "quality gate FAILED: nothing labelled (results/events/news8k_dev.json)"; exit 3; fi
LOG "Bonsai (8-K): labels"
$PY scripts/news8k.py extract || LOG "labels failed"
kill $OLL 2>/dev/null; OLL=""
LOG "code scores W1 (pre-registered)"
$PY scripts/news8k.py test 2>&1 | tee results/events/news8k_test.txt
LOG done
