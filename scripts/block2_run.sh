#!/usr/bin/env bash
# Week plan block 2 (revised 2026-09-26): Bonsai's fact-sheet books on all 1,995 S&P 500 releases of 2024, the year
# they were not found in. Pre-registered: quick (5 d) and mid (20 d) IC > 0; long (120 d) IC < 0 (contrarian).
# Caveat: 2024 is inside Bonsai's training data (pass = weak evidence, fail = informative). Run by hand; resumable.
set -uo pipefail
cd "$(dirname "$0")/../backend"
PY=.venv/bin/python
for H in 5 20 120; do
  $PY scripts/decide_events.py --events data/events/events_sp500_2024.csv --from 2024-01-01 --to 2024-12-31 \
      --features results/events/features_sp500_2024.csv --tag factsheet2024 --horizon $H --explain 0
done
$PY scripts/horizons_eval.py --events data/events/events_sp500_2024.csv --features results/events/features_sp500_2024.csv \
    --with-tag none --without-tag factsheet2024 | tee results/events/horizons_eval_2024.txt
echo BLOCK2 DONE
