# Codex AIRP improvements 1–5 — 2026-09-29

This file is the handoff for Claude Code. The user asked Codex to implement priorities 1–5 and update this file after each completed item. These edits are uncommitted and share the existing checkout. Do not discard other uncommitted work.

## 1. Live/dry run status — complete

Finding: commit `4a655d7` and `docs/PLAN_FORWARD.md` record the user's explicit decision to start live **paper** event decisions early on 29 Sep, covering filings from 30 Sep. `AUTORUN_MODE=live` is intentional; it must not be reverted to dry because the old automatic gate had not passed. No successful live run is yet present in the heartbeat.

Changed `backend/scripts/autorun.py`: the missed-run check now starts from the first eligible live day (30 Sep) even when there is no live heartbeat. Previously it returned no gaps at all until the first live success. Added a synthetic test in `backend/tests/test_autorun.py`.

Verification: 11 autorun tests passed; Ruff passed. No trading or scheduled scripts were run.

## 2. Run health and failure alerts — complete

Changed `backend/scripts/forward_events.py`: before ledger decisions, the forward runner checks SEC acceptance timestamps, recent valid SPY/stock/sector closes, a fact sheet for every new event, and finite model scores. An empty fact-sheet file now has an explicit error. This prevents a parse/data failure from being recorded as a clean run or as an ordinary missed pick.

Changed `backend/scripts/forward_allocator.py` and `backend/app/portfolio/forward.py`: the allocator checks recent valid prices for all four assets, parses the full existing ledger before appending, checks book equity/costs and rejected targets before saving, and records the 5 bp one-way simulator cost assumption in its run record. `autorun.py` stops dependent child steps on a nonzero result. Added synthetic tests in `backend/tests/test_event_health.py`, `backend/tests/test_allocator_health.py`, and `backend/tests/test_autorun.py`.

Verification: 30 focused tests passed; Ruff passed. Two unrelated tests in `tests/test_forward.py` failed because bubblewrap cannot create a namespace in this sandbox. No trading or scheduled scripts were run.
## 3. Paper broker versus simulator audit — complete

Changed `backend/scripts/ai_picks.py`: every non-skipped pair now stores per-leg entry and exit broker status and alerts once for a missing, rejected/canceled, or incomplete hedge. A closed pair with four broker fills records gross broker P&L, simulator net P&L, assumed simulator cost, and fill slippage. The existing per-leg 0.5% fill-gap alert remains. Network uncertainty during submit/refresh is recorded and alerted; an uncertain submit keeps its client order ID for safe refresh rather than blindly resending. The state marks `dry`, `no_keys`, or `checked` audit status. Any broker alert now exits nonzero after the state is saved, so `autorun` cannot mark that event job clean.

Added synthetic `backend/tests/test_pair_audit.py`. Verification: 17 pair/sleeve/broker tests passed; Ruff passed. No Alpaca calls or trading scripts were run.
## 4. One pre-registered stock signal experiment — complete (collector built; outcomes pending)

Frozen one prospective hypothesis in `docs/EXPERIMENT_GUIDANCE_V1.md` before eligible 30 Sep outcomes: among existing high-score Bonsai picks, a code-verified `raised` guidance label may identify better five-day stock-minus-sector returns. The rule, 0.8% round-trip assumed cost, sample gate, weekly cluster bootstrap, and pass wording are fixed. This remains shadow only and cannot move money by itself.

Changed `backend/scripts/forward_events.py` to store the already-verified guidance field on future decision records. Added `backend/scripts/guidance_shadow.py`, scheduled after the event runner by `backend/scripts/autorun.py`, and included its status in `backend/scripts/weekly_review.py`. The separate hash-chained ledger is collected prospectively; no historical outcomes were queried or optimization run. Added synthetic `backend/tests/test_guidance_shadow.py`.

Verification: 24 focused tests passed; Ruff passed. No experiment result exists yet; future forward outcomes are required.
## 5. Long-term shadow cohort monitoring — complete (forward outcomes pending)

Changed `backend/scripts/longterm_picks.py`: a monthly cohort is refused unless it has ten distinct rated stocks. Shadow status now reports pending cohorts, months whose scheduled cohort is missing, overdue unscored cohorts, malformed records, and readiness only after twelve valid scored cohorts with no gaps. The regular run emits `LEARN ALERT` for missing or invalid evidence; it never changes the paper portfolio weight. Added synthetic `backend/tests/test_longterm_health.py`.

Verification: 8 long-term tests passed; Ruff passed. As of 29 Sep, the first scheduled cohort (1 Oct) has not occurred, so there is no long-term return to evaluate. No market data or model was run.

## Final verification and next observations

Combined focused verification on 2026-09-29: 59 tests passed, Ruff passed, `git diff --check` passed. The only broader failures encountered were two pre-existing jail tests in `tests/test_forward.py`; bubblewrap was denied permission to create a namespace in this sandbox. No forward runner, broker, model, registered experiment, data download, systemd change, commit, push, or real-money trade was performed by Codex.

At the next scheduled event run, confirm the live heartbeat records `rc=0`, the event ledger has finite on-time scores, the AI-picks state reports its broker audit, and the new guidance shadow ledger begins collecting eligible signals. On/after 1 Oct 16:00 ET, confirm exactly ten distinct names in the first long-term cohort. Only later matured forward outcomes can answer whether the new guidance condition or monthly stock picks improve returns.
