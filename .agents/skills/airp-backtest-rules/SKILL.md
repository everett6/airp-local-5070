---
name: airp-backtest-rules
description: Rules for writing any strategy, backtest or signal code in airp-local-5070 (backend/app/sandbox, backend/scripts/*_test.py). Use before implementing or reviewing trading-rule logic.
---

# Backtest code rules

1. **No look-ahead.** A decision uses only bars that closed before the order's bar opens. Daily: decide on day t's
   close, fill at t+1's open (see `app/portfolio/forward.py::fill_day`). Intraday: decide on the close of minute
   m, enter at minute m+1's open. Rolling features use `.shift(1)`.
2. **Costs per side**, on every entry and exit, stated in the spec (typical: 1 bp ETFs/large caps intraday, 10 bps
   stocks daily). Return = side × (exit / entry − 1) − 2 × cost.
3. **Calendars:** stock bars are New York time (`America/New_York`); crypto is UTC and trades 365 days (annualize
   with √365 when the spec says so). Drop half days as the spec says (e.g. median trade count < 100).
4. **Universe:** use the list the spec names (e.g. `scripts/intraday_stocks.py::universe()`); note survivorship.
5. **Reuse the stats helpers:** `app/sandbox/intraday.py::sharpe`, `block_ci(r, level=...)`,
   `scripts/daytrade_test.py::stats`, `finish()`. Don't write new bootstrap code.
6. **Never** call `app.sandbox.dsr.register()` or run a trial script (`scripts/daytrade_test.py --rules ...`,
   anything that writes `backend/results/`). Claude runs each registered trial exactly once.
7. The spec in `docs/PLAN_60_V2.md` is fixed before code. If the code can't match it, stop and report; never change
   a threshold, window or cost to make a result better.
