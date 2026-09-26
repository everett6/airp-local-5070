#!/usr/bin/env bash
# Research + decisions on 400 random 2025-26 S&P 500 earnings releases, three books. Run by hand; resumable.
#   1. Jan-v1-4B (vLLM, NVFP4: scripts/vllm_serve.sh) researches each release with the as-of web tools after a
#      code prefetch; Bonsai-27B (Ollama :11435) writes the source-checked brief.
#   2. Bonsai decides BUY/PASS for quick money (5 days), mid term (20) and long term (120), once with the research
#      and once from the fact sheet alone, on the same releases.
#   3. scripts/horizons_eval.py scores each book on its own horizon.
set -euo pipefail
cd "$(dirname "$0")/../backend"
PY=.venv/bin/python
F=results/events/features_sp500_2025_research_Jan-v1-4B-GGUF_Q4_K_M_v2.csv
$PY scripts/research_events.py --limit 400 --backend vllm --workers 6
$PY - <<'P'
import pandas as pd
f = pd.read_csv("results/events/features_sp500_2025_research_Jan-v1-4B-GGUF_Q4_K_M_v2.csv")
base = pd.read_csv("results/events/features_sp500_2025.csv")
base[base.accession.isin(f.accession)].to_csv("results/events/features_sp500_2025_400.csv", index=False)
P
for H in 5 20 120; do
  $PY scripts/decide_events.py --model bonsai-27b:latest --base-url http://127.0.0.1:11435 \
      --events data/events/events_sp500_2025.csv --features $F --tag jan_research --horizon $H --explain 0
  $PY scripts/decide_events.py --model bonsai-27b:latest --base-url http://127.0.0.1:11435 \
      --events data/events/events_sp500_2025.csv --features results/events/features_sp500_2025_400.csv \
      --tag factsheet --horizon $H --explain 0
done
$PY scripts/horizons_eval.py --features $F
echo RESEARCH RUN DONE
