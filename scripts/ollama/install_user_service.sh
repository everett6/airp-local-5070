#!/usr/bin/env bash
# Install and start the tuned Ollama user service (no sudo). Undo: systemctl --user disable --now airp-ollama-tuned
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/.config/systemd/user"
install -m 644 "$here/airp-ollama-tuned.service" "$HOME/.config/systemd/user/airp-ollama-tuned.service"
systemctl --user daemon-reload
systemctl --user enable --now airp-ollama-tuned.service
for _ in $(seq 40); do curl -sf http://127.0.0.1:11435/api/version && echo && exit 0; sleep 0.25; done
echo "airp-ollama-tuned did not answer on :11435" >&2; exit 1
