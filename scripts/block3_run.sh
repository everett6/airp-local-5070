#!/usr/bin/env bash
# Week plan block 3: the three books (5/20/120 days) as one portfolio. Run by hand; resumable.
# 1. Fact-sheet decisions for the whole 1,180-release 2025-26 sample for the 5- and 120-day books (the 20-day book
#    already has them). 2. Combined score per book (walk-forward). 3. Master portfolio with all three books, both
#    sizings, 10 bps costs, vs SPY + crypto sleeve with no stocks.
set -uo pipefail
cd "$(dirname "$0")/../backend"
PY=.venv/bin/python
for H in 5 120; do
  $PY scripts/decide_events.py --events data/events/events_sp500_2025.csv --features results/events/features_sp500_2025.csv \
      --tag factsheet --horizon $H --explain 0
done
for H in 5 20 120; do $PY scripts/combine_scores.py --horizon $H | tail -n 12 > results/events/combined_eval_h$H.txt; done
for SZ in kelly top5th; do
  $PY scripts/master_portfolio.py full --start 2024-03-01 --events data/events/events_sp500_2024_2026.csv \
      --book results/events/decide_combined_h5.jsonl:5 --book results/events/decide_combined.jsonl:20 \
      --book results/events/decide_combined_h120.jsonl:120 --min-calibration 150 --sizing $SZ --cost-bps 10 \
      --tag _three_books_$SZ | tee results/master_full_three_books_$SZ.txt
done
echo BLOCK3 DONE
