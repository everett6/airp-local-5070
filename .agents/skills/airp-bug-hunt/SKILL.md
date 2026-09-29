---
name: airp-bug-hunt
description: Read-only review of airp-local-5070 changes for real defects in trading, backtest, ledger or scheduling code. Use when asked to review, audit or debug code in this repo.
---

# Bug hunt checklist (report, don't edit)

Report only real defects, each with file:line, one sentence, a concrete failing input and severity. Check:

- **Look-ahead:** features without `.shift(1)`, same-bar decide-and-fill, using today's close before it exists,
  outcomes read before they matured.
- **Off-by-one:** entry index vs decision day, holding period length, week/month boundaries, first period without
  history, last day of a period.
- **Double counting:** costs charged twice or never; returns counted in two slots on the same interval.
- **Time zones:** New York vs UTC dates, half days, weekends for crypto vs stocks.
- **Survivorship / universe:** today's index list used for the past.
- **Statistics:** repeated looks at a fixed test ("peeking"), wrong annualization (√252 vs √365), bootstrap
  blocks too short for overlapping returns.
- **Spec match:** compare against the section in `docs/PLAN_60_V2.md`; a mismatch is a finding even if tests pass.
- **Ops:** anything that could make a scheduled run fail or skip (`scripts/autorun.py`, `forward_events.gpu_free`).
Don't run trial scripts, the GPU, or anything that writes `backend/results/`. Say "none" if nothing is real.
