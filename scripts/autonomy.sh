#!/usr/bin/env bash
# Autonomy for the paper book (docs/PLAN_FORWARD.md "Decisions"). systemd USER timers only; no system service.
#   scripts/autonomy.sh install     # install + start the timers (mode stays as set; default dry)
#   scripts/autonomy.sh dry|live    # dry: own folders, no git; live: the real ledgers, commit + push
#   scripts/autonomy.sh status      # timers, mode, last heartbeats and alerts
#   scripts/autonomy.sh uninstall   # stop and remove the timers
# The kill switch still works at any time: backend/.venv/bin/python backend/scripts/forward_allocator.py --halt "why"
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
UNITS=(airp-events airp-allocator airp-review airp-check)
DEST="$HOME/.config/systemd/user"
FWD="$ROOT/backend/results/forward"
case "${1:-status}" in
  install)
    mkdir -p "$DEST"
    for u in "${UNITS[@]}"; do cp "$ROOT/deploy/systemd/$u.service" "$ROOT/deploy/systemd/$u.timer" "$DEST/"; done
    systemctl --user daemon-reload
    for u in "${UNITS[@]}"; do systemctl --user enable --now "$u.timer"; done
    # not a timer: runs once at boot and does nothing unless a restart cut a scheduled run off (autorun.py resume)
    cp "$ROOT/deploy/systemd/airp-resume.service" "$DEST/"; systemctl --user daemon-reload
    systemctl --user enable airp-resume.service
    [ -f "$FWD/AUTORUN_MODE" ] || echo dry > "$FWD/AUTORUN_MODE"
    echo "installed; mode: $(cat "$FWD/AUTORUN_MODE")" ;;
  dry|live)
    mkdir -p "$FWD"; echo "$1" > "$FWD/AUTORUN_MODE"; echo "mode: $1" ;;
  status)
    systemctl --user list-timers 'airp-*' --no-pager || true
    echo "mode: $(cat "$FWD/AUTORUN_MODE" 2>/dev/null || echo 'dry (no mode file)')"
    echo "--- last heartbeats"; tail -5 "$FWD/heartbeat.jsonl" 2>/dev/null || echo none
    echo "--- last alerts"; tail -5 "$FWD/alerts.jsonl" 2>/dev/null || echo none ;;
  uninstall)
    for u in "${UNITS[@]}"; do systemctl --user disable --now "$u.timer" 2>/dev/null || true; rm -f "$DEST/$u.service" "$DEST/$u.timer"; done
    systemctl --user disable airp-resume.service 2>/dev/null || true; rm -f "$DEST/airp-resume.service"
    systemctl --user daemon-reload; echo "timers removed" ;;
  *) echo "usage: $0 install|dry|live|status|uninstall"; exit 2 ;;
esac
