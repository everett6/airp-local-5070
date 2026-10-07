# Overnight plan, 6 Oct 22:00 to 7 Oct 07:00 PDT (autonomous, user away)

Budgets at start: Claude 5-hour 60% used (resets 00:20 PDT), weekly 67% used (resets 8 Oct 07:00 PDT).
Codex: new weekly window since 6 Oct 15:07 PDT; the user's floor is 75% left, so Codex may use at most 25% tonight.
Codex model gpt-6.1-sol (allowed by the user for complex work), medium effort; Claude keeps broker/money code.

| # | When (PDT) | Task | Who | Done when |
|---|---|---|---|---|
| 1 | next research gap | #43: Night's concrete why-no-trade reasons; test; restart Night | Claude | test passes, Night running again |
| 2 | 22:15 | #39 regime split and #47 turnover / spread paid in fund_report.py, with tests | Codex | pytest + ruff pass, Claude reviewed |
| 3 | 22:15 | Read-only review of account_book.py and algo_engine.py trade path before Day's first live open | Codex | findings list; Claude fixes real ones (tests first) |
| 4 | 06:15 | Pre-open check: Day state, reconcile clean, feed connected, limits | Claude | Day ready |
| 5 | 06:30-07:00 | Watch Day's first live paper orders; fix any failure from the real error | Claude | orders filled or explained; incidents empty |
| 6 | 07:00 | Summary for the user + memory | Claude | |

Rules kept: no new trials planned (any new test is pre-registered here first), no stopping research mid-batch,
no leverage changes, no downloads, no shutdown.
