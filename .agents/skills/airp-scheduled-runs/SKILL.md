---
name: airp-scheduled-runs
description: Diagnose a failed or suspicious scheduled run (airp-events, airp-allocator, airp-review, airp-check systemd user timers) in airp-local-5070. Use when a heartbeat shows rc != 0, an alert fired, or a run looks wrong.
---

# Scheduled-run triage

1. Where to look (read-only): `backend/results/forward/heartbeat.jsonl` (rc, log name), `alerts.jsonl`,
   `backend/results/forward/logs/<job>_<mode>_<time>.log`, and `journalctl --user -u airp-events -n 50`.
2. Jobs are `scripts/autorun.py <job>`; the command list per job is `commands()`; dry mode uses `DRY` folders.
   Each step prints `LEARN ALERT` / `BROKER ALERT` lines that autorun turns into alerts.
3. Reproduce on a copy: copy the dry folder to a temp dir and run the failing script with `--dir <tmp>` and
   `--no-gpu`/`--dry`. Status modes (`--status`) are read-only.
4. **Never** trigger `autorun.py events|allocator` by hand (it would count toward the go-live check), never edit
   heartbeats or ledgers, never add systemd timers or change power/system settings.
5. Report: the failing step, the root cause from the actual error text, and a proposed fix with a test. Known past
   causes: sleep inhibitor refused at boot (`can_inhibit`), GPU seen as busy by a desktop app (`gpu_busy`), network
   not up at boot (phone push).
