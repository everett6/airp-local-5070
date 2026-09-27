#!/usr/bin/env bash
# Breadth test GPU work (docs/PLAN_60_V2.md "Breadth test"): the number reader on the S&P 400/600 2025-26 releases,
# fact sheets, then Bonsai's 1-week decisions, then the pre-registered evaluation. One model on the GPU at a time.
# Resumable. Manual run, not a service:
#   systemd-inhibit --what=idle:sleep scripts/breadth_run.sh
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT/backend"
PY=.venv/bin/python
EV=data/events/events_breadth_2025.csv
EX=results/events/extract_qwen3_8b_breadth.jsonl
LOG() { echo "$(date '+%F %T') $*"; }
OLL=""
trap '[ -n "$OLL" ] && kill $OLL 2>/dev/null' EXIT
serve() {  # port models_dir parallel
  OLLAMA_MODELS="$2" OLLAMA_NOPRUNE=1 OLLAMA_HOST="127.0.0.1:$1" OLLAMA_NUM_PARALLEL=$3 OLLAMA_MAX_LOADED_MODELS=1 \
    OLLAMA_FLASH_ATTENTION=1 nohup setsid ollama serve > "results/events/ollama_breadth_$1.log" 2>&1 &
  OLL=$!
  until curl -s -m 2 "127.0.0.1:$1/api/tags" > /dev/null; do sleep 2; done
}
stop() { kill $OLL 2>/dev/null; wait $OLL 2>/dev/null; OLL=""; sleep 3; }

LOG "waiting for the press-release fetch"
while pgrep -f "[e]xtract_events.py fetch --events $EV" > /dev/null; do sleep 30; done
LOG "reader (qwen3:8b, numbers only, code-checked)"
serve 11437 /usr/share/ollama/.ollama/models 4
$PY scripts/extract_events.py extract --events $EV --from 2025-01-01 --model qwen3:8b --base-url http://127.0.0.1:11437 \
  --parallel 4 --out $EX || LOG "reader failed"
stop
LOG "fact sheets"
$PY scripts/build_features.py --events $EV --extract $EX --name breadth_2025 || { LOG "features failed"; exit 1; }
LOG "Bonsai 1-week decisions"
serve 11435 "$HOME/.ollama/models" 3
$PY scripts/decide_events.py --events $EV --extract $EX --features results/events/features_breadth_2025.csv --tag breadth \
  --horizon 5 --explain 0 || LOG "decisions failed"
stop
LOG "evaluation (pre-registered)"
$PY scripts/breadth_eval.py 2>&1 | grep -v "Failed\|delisted\|^\[" | tee results/events/breadth_eval.txt
LOG done
