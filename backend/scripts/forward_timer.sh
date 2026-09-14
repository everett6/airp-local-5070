#!/usr/bin/env bash
# Install, remove, or inspect the forward-test catch-up timer (systemd --user, no root).
#
#   scripts/forward_timer.sh install     # hourly + 3 min after boot; missed runs catch up (Persistent=true)
#   scripts/forward_timer.sh uninstall   # stop and remove the units
#   scripts/forward_timer.sh status      # next run, last run, ledger scoreboard
#
# The job (python -m app.forward.run) is idempotent and takes a lock, so running it
# hourly only acts when a decision or an outcome is actually due.
# User timers run while you are logged in. To run them while logged out too:
#   loginctl enable-linger "$USER"
set -euo pipefail

BACKEND="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
NAME="airp-forward"

case "${1:-status}" in
  install)
    [ -x "$BACKEND/.venv/bin/python" ] || { echo "missing $BACKEND/.venv (set up the backend first)"; exit 1; }
    mkdir -p "$UNIT_DIR"
    cat > "$UNIT_DIR/$NAME.service" <<EOF
[Unit]
Description=AIRP forward test: log weekly decisions before the open, append outcomes
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$BACKEND
Environment=PYTHONUNBUFFERED=1
ExecStart=$BACKEND/.venv/bin/python -m app.forward.run
TimeoutStartSec=3h
Nice=5
EOF
    cat > "$UNIT_DIR/$NAME.timer" <<EOF
[Unit]
Description=Run the AIRP forward test hourly and soon after boot

[Timer]
OnBootSec=3min
OnCalendar=hourly
RandomizedDelaySec=4min
Persistent=true

[Install]
WantedBy=timers.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now "$NAME.timer"
    systemctl --user list-timers "$NAME.timer" --no-pager
    ;;
  uninstall)
    systemctl --user disable --now "$NAME.timer" 2>/dev/null || true
    rm -f "$UNIT_DIR/$NAME.service" "$UNIT_DIR/$NAME.timer"
    systemctl --user daemon-reload
    echo "removed $NAME timer and service"
    ;;
  status)
    systemctl --user list-timers "$NAME.timer" --no-pager || true
    journalctl --user -u "$NAME.service" -n 5 --no-pager 2>/dev/null || true
    cd "$BACKEND" && .venv/bin/python -m app.forward.run --status
    ;;
  *)
    echo "usage: $0 install|uninstall|status"; exit 2 ;;
esac
