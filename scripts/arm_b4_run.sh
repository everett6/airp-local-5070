#!/usr/bin/env bash
# Arm B4 (docs/PLAN_60_V2.md "Arm B4", spec fixed before any B4 label). Stages: Bonsai arm-A labels (P2) while the
# news warm-up runs -> stop rule (news coverage >= 50%) -> Jan gather (vLLM) -> audit -> Bonsai PROMPT_R labels ->
# the one test. GPU stages never overlap the live runs: they stop before each quiet window and resume after it.
# Resumable. Manual run, not a service:  systemd-inhibit --what=idle:sleep scripts/arm_b4_run.sh
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT/backend"
PY=.venv/bin/python
LOG() { echo "$(date '+%F %T') $*"; }
SRV=""
stop_srv() { [ -n "$SRV" ] && { kill -- -"$SRV" 2>/dev/null; wait "$SRV" 2>/dev/null; }; SRV=""; }
trap stop_srv EXIT
gpu_idle() { until ! nvidia-smi --query-compute-apps=process_name --format=csv,noheader | grep -qi "python\|vllm\|ollama"; do sleep 10; done; }
# quiet windows (local time): the live event runs at 05:45 / 15:30, and Thu 1 Oct's first long-term + theme cohorts
QUIET='
import sys; from datetime import datetime, timedelta
now = datetime.now()
def wins(d):
    w = [(d.replace(hour=5, minute=30), d.replace(hour=6, minute=30)), (d.replace(hour=15, minute=15), d.replace(hour=16, minute=30))]
    if d.date().isoformat() == "2026-10-01": w[1] = (w[1][0], d.replace(hour=18, minute=0))
    return w
d0 = now.replace(second=0, microsecond=0)
ws = [w for k in range(3) for w in wins(d0 + timedelta(days=k))]
inside = [e for s, e in ws if s <= now < e]
if sys.argv[1] == "wait": print(int((inside[0] - now).total_seconds()) + 60 if inside else 0)
else: print(min(int((s - now).total_seconds()) for s, e in ws if s > now))'
quiet_wait() { local s; s=$($PY -c "$QUIET" wait); [ "$s" -gt 0 ] && { LOG "quiet window: pausing ${s}s"; sleep "$s"; }; }
until_quiet() { $PY -c "$QUIET" left; }
start_ollama() {
  OLLAMA_MODELS=$HOME/.ollama/models OLLAMA_NOPRUNE=1 OLLAMA_HOST=127.0.0.1:11435 OLLAMA_NUM_PARALLEL=3 \
    OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 \
    setsid ollama serve >> results/events/ollama_b4.log 2>&1 &
  SRV=$!; until curl -s -m 2 127.0.0.1:11435/api/tags > /dev/null; do sleep 2; done; }
start_vllm() {
  MAX_SEQS=${MAX_SEQS:-16} GPU_UTIL=${GPU_UTIL:-0.85} setsid "$ROOT/scripts/vllm_serve.sh" >> results/events/vllm_b4.log 2>&1 &
  SRV=$!; until curl -s -m 2 127.0.0.1:8000/v1/models > /dev/null; do
    kill -0 "$SRV" 2>/dev/null || { LOG "vLLM exited"; tail -20 results/events/vllm_b4.log; return 1; }; sleep 5; done; }
# a stage stopped mid-write can leave a half line at the end of a label file: drop it before resuming
trim() { for f in results/events/llm_fields_b4.jsonl results/events/llm_fields_research_b4_b4.jsonl; do [ -f "$f" ] && $PY -c "
import json, sys; p = sys.argv[1]; L = open(p).read().splitlines(True)
try: L and json.loads(L[-1])
except ValueError: open(p, 'w').writelines(L[:-1]); print('dropped a half line in', p)" "$f"; done; }
# gpu_stage NAME SERVER CMD...: run CMD with SERVER up, stopping before each quiet window; resume until it exits 0
gpu_stage() {
  local name=$1 server=$2 fails=0 rc; shift 2
  while :; do
    quiet_wait; gpu_idle
    LOG "$name: start"
    $server || { fails=$((fails + 1)); [ $fails -ge 2 ] && return 1; continue; }
    timeout --signal=TERM "$(until_quiet)" "$@"; rc=$?
    stop_srv; sleep 5; trim
    [ $rc -eq 0 ] && { LOG "$name: done"; return 0; }
    [ $rc -eq 124 ] && { LOG "$name: paused for a quiet window"; continue; }
    fails=$((fails + 1)); LOG "$name: failed (rc $rc, $fails)"; [ $fails -ge 2 ] && return 1
  done
}

[ -f data/events/events_b4_2026.csv ] || $PY scripts/b4_prep.py
gpu_stage "Bonsai arm A labels (P2)" start_ollama $PY scripts/llm_fields.py extract-b4 || { LOG "arm A labels failed"; exit 1; }
LOG "waiting for the news warm-up"
while pgrep -f "[w]arm_news.py b4" > /dev/null; do sleep 60; done
COV=$($PY -c "
import json; r=[json.loads(x) for x in open('results/events/warm_news.jsonl') if '\"sample\": \"b4\"' in x]
print(round(sum(x['status']!='none' for x in r)/max(1,len(r)),3), len(r))")
LOG "B4 news coverage after warm-up (share, releases): $COV"
if ! $PY -c "import sys; sys.exit(0 if ${COV%% *} >= 0.5 and ${COV##* } >= 2800 else 1)"; then
  LOG "STOP RULE: news coverage below 50% (or warm-up incomplete); B4 not run"; exit 3; fi
gpu_stage "Jan gather (_v3b4)" start_vllm $PY scripts/research_events.py --features results/events/features_b4_2026.csv \
  --events data/events/events_b4_2026.csv --run-tag _v3b4 --backend vllm --workers 16 --phase gather \
  || { LOG "Jan gather failed"; exit 1; }
$PY scripts/research_audit.py results/events_research_Jan-v1-4B-GGUF_Q4_K_M_v3b4 | tail -3
gpu_stage "Bonsai arm B2 labels (PROMPT_R)" start_ollama $PY scripts/llm_fields.py extract-research-b4 \
  || { LOG "arm B4 research labels failed"; exit 1; }
LOG "code scores arm B4 (pre-registered, one trial)"
$PY scripts/llm_fields.py test-b4 2>&1 | tee results/events/llm_fields_research_b4_test.txt
LOG done
