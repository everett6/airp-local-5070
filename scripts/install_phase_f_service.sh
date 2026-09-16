#!/usr/bin/env bash
# Enable the one-shot Phase F pipeline so it starts after the next boot/login. Logs: journalctl --user -u airp-phase-f
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/.config/systemd/user"
install -m 644 "$here/airp-phase-f.service" "$HOME/.config/systemd/user/airp-phase-f.service"
systemctl --user daemon-reload
systemctl --user enable airp-phase-f.service
