#!/usr/bin/env bash
# The 27 Sep night's GPU queue, one job at a time: wait for research v3, then the spike cause briefs, then the
# breadth test. Manual run, not a service:
#   systemd-inhibit --what=idle:sleep scripts/night_0927.sh
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
LOG() { echo "$(date '+%F %T') $*"; }
LOG "waiting for research v3"
while pgrep -f "[r]esearch_v3_run.sh" > /dev/null; do sleep 30; done
until ! nvidia-smi --query-compute-apps=process_name --format=csv,noheader | grep -qi "python\|vllm\|ollama"; do sleep 5; done
LOG "spike cause briefs"
"$ROOT/scripts/spike_briefs_run.sh"
sleep 5
LOG "breadth test"
"$ROOT/scripts/breadth_run.sh"
LOG "night queue done"
