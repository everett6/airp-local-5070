#!/usr/bin/env bash
# Paper portfolio viewer (read-only). Started by hand; stop it with Ctrl+C.
#   scripts/portfolio_ui.sh          # this PC only: http://localhost:8502
#   scripts/portfolio_ui.sh --lan    # also reachable from your phone on the same Wi-Fi: http://<this PC's IP>:8502
set -euo pipefail
cd "$(dirname "$0")/../backend"
addr=127.0.0.1
[[ "${1:-}" == "--lan" ]] && addr=0.0.0.0 && echo "phone: http://$(hostname -I | awk '{print $1}'):8502"
exec .venv/bin/streamlit run app/dashboard/portfolio.py --server.port 8502 --server.address "$addr" \
  --server.headless true --browser.gatherUsageStats false
