#!/usr/bin/env bash
# Bonsai on the cleaned fact sheets for the new books (3 months, 1 year, 2 years), 2025-26 and 2024; the 1-week,
# 1-month and 6-month books already have their decisions (scripts/secchk_run.sh). Manual run, not a service:
#   systemd-inhibit --what=idle:sleep scripts/horizons6_run.sh
set -u
cd "$(dirname "$0")/../backend"
PY=.venv/bin/python
LOG() { echo "$(date '+%F %T') $*"; }

OLLAMA_MODELS=$HOME/.ollama/models OLLAMA_NOPRUNE=1 OLLAMA_HOST=127.0.0.1:11435 OLLAMA_NUM_PARALLEL=3 \
  OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 \
  nohup setsid ollama serve > results/events/ollama_horizons6.log 2>&1 &
OLL=$!
trap 'kill $OLL 2>/dev/null' EXIT
until curl -s -m 2 127.0.0.1:11435/api/tags > /dev/null; do sleep 2; done

for H in 63 252 504; do
  LOG "2025-26 h$H"
  $PY scripts/decide_events.py --events data/events/events_sp500_2025.csv \
      --features results/events/features_sp500_2025_secchk.csv --tag factsheet_secchk --horizon $H --explain 0 \
      || LOG "2025 h$H failed"
  LOG "2024 h$H"
  $PY scripts/decide_events.py --events data/events/events_sp500_2024.csv --from 2024-01-01 --to 2024-12-31 \
      --features results/events/features_sp500_2024_secchk.csv --tag factsheet2024_secchk --horizon $H --explain 0 \
      || LOG "2024 h$H failed"
done
$PY scripts/horizons6_eval.py | tee results/events/horizons6_eval.txt
LOG done
