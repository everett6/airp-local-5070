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

## Progress (23:30 PDT): the user approved the order-path edits
- 35ed788e: findings 1-7 fixed with fake-broker tests (unknown-outcome gate, 5xx, closes sized under the lock
  counting working orders, unique flatten IDs, no replay of partial fills, drawdown kill blocks Day, Day gross clamp).
- 1412e14f: Day closes on any exit incl. signals; additions skipped when slow reductions made the decision late.
- Day restarted with the fixes (LIVE paper, waiting for the open). 908 tests pass.
- Night: the drawdown kill now uses logged, verified closes for both strategies, and the 4% breaker is latched for
  the session; applied at the next research gap with a Night restart (tests run first; reverted if any fail).

## Result at 07:05 PDT (end of the window)
- Night restarted 23:03 with review #8/#11 (583e490b); 908 tests passed before the restart. Reconciled clean.
- Pre-open check 06:15: no incidents, no account gate, no open intents, LIVE on, no PAUSE/STOP/HALT/KILLED.
- Day (live paper) at the open: 6 decisions by 10:00 ET, 1-4 s after each bar, 0 orders. Every ETF's predicted
  move (0.01-0.16 bp) is below its round trip (1-3 bp), so the cost rule keeps it flat. The failed E1 model sees no
  edge after costs; nothing was changed to make it trade.
- Night at the open: 129 orders submitted; 2,325 attempts refused by the account risk check, mostly "spread
  exceeds limit" (150 bp) on 91 mid-cap names whose free IEX quote is wide at the open; they retry every loop.
  Not caused by tonight's changes (the risk check is unchanged; 6 Oct had the same reason on 6 names). Limits were
  not loosened. Open item: retry such names less often (noise only) or wait for spreads to narrow after the open.
- App rebuilt with the Fund control additions. Codex use tonight: two gpt-6.1-sol medium tasks; weekly usage 16% at
  the last check (84% left).
