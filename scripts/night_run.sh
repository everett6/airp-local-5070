#!/usr/bin/env bash
# Overnight run (docs/WEEK_PLAN.md, block 1). Run by hand; every step is resumable. Holds a sleep inhibitor while it
# runs. Needs: vLLM serving Jan NVFP4 (scripts/vllm_serve.sh, GPU_UTIL=0.40) and Ollama :11435 (Bonsai, 3 slots).
#
#   DEADLINE=2026-09-26T05:15 scripts/night_run.sh
#
# 1. Research (Jan on vLLM + tools, Bonsai briefs) on the 1,180-release 2025-26 sample, in its fixed random order,
#    until DEADLINE; the first 400 finish first, the rest as time allows.
# 2. Bonsai BUY/PASS for the three books (5/20/120 days), with the research and from the fact sheet alone, on
#    exactly the researched releases.
# 3. Score each book on its own horizon, and run the master portfolio per book for both arms.
[ -z "${INHIBITED:-}" ] && exec systemd-inhibit --what=idle:sleep --who=airp --why="overnight research run" \
  env INHIBITED=1 "$0" "$@"
cd "$(dirname "$0")/../backend"
PY=.venv/bin/python
DEADLINE=${DEADLINE:-}
F=results/events/features_sp500_2025_research_Jan-v1-4B-GGUF_Q4_K_M_v2.csv
BASE=results/events/features_sp500_2025_researched_base.csv
LOG() { echo "[$(date '+%F %T')] $*"; }

LOG "step 1: research until ${DEADLINE:-done}"
for TRY in 1 2 3; do  # resumable: a failed try (e.g. a GPU check) continues where it stopped
  $PY scripts/research_events.py --limit 1180 --backend vllm --workers 6 --deadline "$DEADLINE" && break
  LOG "research try $TRY failed"; sleep 60
done
[ -f "$F" ] || { LOG "no research table: stopping"; exit 1; }

LOG "step 2: decisions, three books, with and without research (same releases)"
$PY - <<P
import pandas as pd
f = pd.read_csv("$F")
base = pd.read_csv("results/events/features_sp500_2025.csv")
base[base.accession.isin(f.accession)].to_csv("$BASE", index=False)
print(len(f), "researched releases")
P
for H in 5 20 120; do
  $PY scripts/decide_events.py --events data/events/events_sp500_2025.csv --features $F --tag jan_research \
      --horizon $H --explain 0 || LOG "decide research h$H failed"
  $PY scripts/decide_events.py --events data/events/events_sp500_2025.csv --features $BASE --tag factsheet \
      --horizon $H --explain 0 || LOG "decide factsheet h$H failed"
done

LOG "step 3: scores and master portfolios"
$PY scripts/horizons_eval.py --features $F | tee results/events/horizons_eval.txt
for H in 5 20 120; do
  S=$([ $H = 20 ] && echo "" || echo "_h$H")
  for ARM in jan_research factsheet; do
    $PY scripts/master_portfolio.py full --start 2025-01-02 --events data/events/events_sp500_2025.csv \
        --decide results/events/decide_bonsai-27b_latest_${ARM}${S}.jsonl --min-calibration 100 --horizon $H \
        --tag _${ARM}_h$H | tee results/master_full_${ARM}_h$H.txt || LOG "master $ARM h$H failed"
  done
done
LOG "NIGHT DONE"
