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

## Progress (22:45 PDT)
- 1 done: f94463b2 (#43), Night restarted at a research gap.
- 2 done: ec8deb8c (#39, #47) by Codex, reviewed; 900 tests pass.
- 3 done: Codex review of the order path found 12 issues; Claude checked 10 as real. The fixes were BLOCKED by the
  auto-mode permission check (editing shared live-trading code), so they wait for the user's approval:
  1. A lost submit reply (or an HTTP 5xx) does not block other new risk; fix: account-wide gate on unknown-outcome
     orders, 5xx treated as unknown, not rejected.
  2. Flatten sizes its close outside the account lock (watchdog vs Day race could go short); fix: size closes from
     holdings read under the lock.
  3. Day sets "flattened" before the end-of-window flatten is verified, so the clock guard may skip it; fix: set it
     only after a verified flatten (live).
  4. Flatten order IDs repeat within a session, so a second close of a reopened position can be skipped; fix: a
     time-stamped flatten ID.
  5. A partly filled, then canceled attempt can be retried at full size; fix: retry only zero-fill attempts.
  6. Night's 35% drawdown kill does not stop Day; fix: the same gate checks KILLED and HALT.
  7. Day's gross from config is not capped at 3.0 on a live reload; fix: clamp to the ceiling.
  Lower: SIGTERM/cancel path skips Day's close-on-stop; the drawdown kill closes positions outside the intent log;
  quote freshness is checked once per batch; Night's 4% breaker is not latched for the session.
