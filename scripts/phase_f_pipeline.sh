#!/usr/bin/env bash
# Phase F GPU chain, one job at a time (docs/EXECUTION_PLAN.md, Phase F). Safe to re-run: finished steps replay
# from caches or are skipped. Run it in a terminal you keep open; if it stops, run it again.
# Everything uses the standard Ollama on :11434.
set -euo pipefail
cd "$(dirname "$0")/../backend"
PY=.venv/bin/python
OLLAMA=http://127.0.0.1:11434
log() { echo "[phase-f $(date '+%F %T')] $*"; }

nvidia-smi -L >/dev/null 2>&1 || { log "GPU not available (reboot needed after an Xid 79?)"; exit 1; }
curl -sf "$OLLAMA/api/version" >/dev/null || { log "Ollama not answering on $OLLAMA"; exit 1; }
# free the GPU before steps that load something else (Kronos) or switch runs
unload() { curl -s "$OLLAMA/api/generate" -d '{"model":"qwen3:8b","keep_alive":0}' >/dev/null || true; sleep 3; }

if [ ! -f results/walkforward_v5_fund_top100.json ]; then
  log "v5"; $PY -m app.sandbox.walkforward --config configs/v5_fund_top100.toml --force
fi
if [ ! -f results/walkforward_v6_fund_rank20d.json ]; then
  unload; log "v6"; $PY -m app.sandbox.walkforward --config configs/v6_fund_rank20d.toml --force
fi
if [ ! -f results/kronos_v7_phase_f.meta.json ]; then
  [ -d third_party/Kronos ] && [ -x .venv-kronos/bin/python ] || scripts/setup_kronos.sh
  unload; log "Kronos forecasts"; .venv-kronos/bin/python scripts/kronos_forecasts.py configs/v7_phase_f.toml
fi
if [ ! -f results/lap_v7_phase_f.jsonl ]; then
  unload; log "LAP probe"; $PY -m app.sandbox.walkforward --config configs/v7_phase_f.toml --probe-lap
fi
if [ ! -f results/walkforward_v7_phase_f.json ]; then
  unload; log "v7"; $PY -m app.sandbox.walkforward --config configs/v7_phase_f.toml
fi
unload
log "LAP interaction test"; $PY scripts/lap_test.py v7_phase_f llm_fund_lp > /dev/null
log "criteria"; $PY scripts/evaluate_criteria.py v5_fund_top100 v6_fund_rank20d v7_phase_f | tee results/criteria_summary.md
log "done"
