# Fund control: the 50-point review, item by item (7 Oct 2026)

Rules: docs/PLAN_60_V2.md, sections "O1" and "O1 addendum", written before any code.

Main code:
- `backend/app/portfolio/account_book.py`: the one risk authority.
- `backend/scripts/algo_engine.py`: Day.
- `backend/scripts/full_auto.py`: Night.
- `backend/app/sandbox/research_quality.py`: research checks.
- `backend/scripts/fund_report.py` and `fact_review.py`: reports.
- The app's **Fund control** page.

Status key:
- **done**: built and tested.
- **partial**: built, with the limit noted.
- **you**: needs your input.

## Operating states and fallbacks (#16, #17)

| State | What may be sent |
|---|---|
| waiting | nothing (market closed, or outside Day's window) |
| ready | nothing yet; trading starts at the next decision |
| trading | entries and exits |
| reduce_only | only orders that shrink positions (circuit breaker, or your pause) |
| reconciling | nothing until local records and the broker agree |
| recovering | nothing until fresh data, a clean reconciliation and valid limits |
| halted | nothing (kill switch, 35% drawdown, Day's 5% daily stop, limits above the ceilings) |

| Failure | Fallback |
|---|---|
| GPU / Jan / Bonsai fails | No new research calls. Existing positions keep being managed. The failed research is retried later (twice in 24 h at most). |
| Broker read or submit fails, or a reply is lost | The order stays recorded as intended or sent. Reconciling: look the order up by its ID and never send it twice. No new orders until reconciled. |
| Market data stream stops (Day) | No decisions on stale bars. No bars for 10 minutes inside the window: flat (by the clock, not by data). |
| Day process hangs or dies inside its window | Night's watchdog flattens Day's symbols (reduce-only) and opens an incident. |
| A flatten that the broker does not confirm | The incident stays open, it retries every minute, and no new entries are made. |
| PC restart | Locks are released automatically. Latches, intents and incidents are on disk. Both workers reconcile before trading. |

## The 50 items

| # | Item | Status | Where / how |
|---|---|---|---|
| 1 | One account-wide risk authority | done | `send_batch` under one file lock; both autopilots use it |
| 2 | Pending orders in exposure | done | The account risk check counts open orders at worst-case fills, for both autopilots together |
| 3 | Stable order IDs, looked up before retry | done | `client_id(strategy, session, decision, symbol, intent)`; numbered retries only after a final unfilled attempt |
| 4 | Intent on disk before submission | done | `intents.jsonl`, fsync'd before Alpaca is contacted; a lost reply stays "ambiguous" until reconciled |
| 5 | Reductions filled before additions | done | Day: wait for a final state, then re-read holdings. Night: reductions settle first. |
| 6 | Every order state | done | Partial, rejected, expired and canceled are followed by polling the order by ID (not the trade_updates stream). A working order past its time is canceled, then read again. |
| 7 | Risk from broker holdings | done | Day's breaker and pause use broker positions, not the simulated book |
| 8 | Position ownership | done | Day owns the 14 ETFs, Night everything else; enforced on every order; the watchdog may only reduce |
| 9 | Reconcile before opening risk | done | At start and after any error; entries blocked until clean |
| 10 | Verified flattening | done | `flatten` checks the broker until positions and orders are gone, else an incident |
| 11 | Clock-driven session exit | done | Day's guard task runs every 5 s on the clock, independent of bars |
| 12 | Failed model not shown as qualified | done | Day is labelled "failed strategy, paper experiment" everywhere; leverage stays your choice |
| 13 | Size for loss, not labels | done | "Strong" no longer doubles size; book gross kept; ATR risk sizing stays |
| 14 | Correlated exposure | done | Theme caps (R1), beta-weighted net within ±30%, exposure by owner, theme and beta on the dashboard |
| 15 | Liquidity-aware execution | done | Day: IOC limit orders 3 bp past the quote, quote 5 s or newer, spread 5 bp or less. Night: at most 1% of daily dollar volume; the existing quote-age and spread checks stay. |
| 16 | Explicit operating states | done | Table above; every change logged with its reason |
| 17 | Fallback per dependency | done | Table above |
| 18 | Persisted recovery state | done | Day's latches (`day_state.json`), intents, incidents and states survive restarts |
| 19 | Health check before resuming | done | Fresh data, clean reconciliation, readable account, limits under the ceilings |
| 20 | Bounded retries with backoff | done | Reads retry rate limits with backoff. A submission is never blindly repeated. Up to 5 numbered re-attempts, only after the previous one is final. |
| 21 | Independent watchdog | done | Night watches Day's heartbeat and lock |
| 22 | One owner per worker | done | File locks per worker (released on death) plus one account lock |
| 23 | Deadlines and queue delays | done | Day skips decisions over 30 s late; Night records research-to-order time per call |
| 24 | Failure rehearsals asserting broker state | done | `tests/test_account_book.py`, `tests/test_algo_engine.py` (fake broker): lost reply, crash before send, partial fill, reject, stuck close, deadline with no data, restart |
| 25 | Restore and rollback | done | Backups now include research memory and fact checks; a restored intent log reconciles and does not replay (tested) |
| 26 | Defined prediction target | done | Stock minus beta × QQQ, from the entry session's open to the exit session's close |
| 27 | News separate from timing | done | Timing gate: no entry after a move of 1.5 ATR or more in the call's direction since the research |
| 28 | Compatible data feeds | partial | Checked: most features match (correlation 0.93–0.99), but the volume feature does not (0.58) and the 1-minute return only partly (0.80). Shown on Fund control. Fixing it needs a paid real-time feed, so it is not done. |
| 29 | Evidence frozen at decision time | done | Pages dated after the research are dropped (already in place); every record gets a timestamp audit |
| 30 | Human-verified extraction benchmark | you | The trap benchmark already exists (`benchmarks/extraction`, waiting for your sign-off on the Engineering page). New: a fact-check queue on Fund control. Accuracy is unknown until you mark facts. |
| 31 | Syndicated news counted once | done | Text-overlap groups; coverage needs two independent non-SEC sources |
| 32 | Catalyst freshness | done | Bonsai's card tags each fact NEW, KNOWN since a date, or OLD |
| 33 | Net opportunity | done | Kelly on returns net of a 10 bp round trip; Day trades only when the predicted move beats the round trip |
| 34 | Calibration | done | Hit rate and mean by label (strong bull, bull, …) |
| 35 | Abstention measured | done | The size of the moves missed on no-side calls |
| 36 | Chronological validation with overlap controls | done | Walk-forward in E1/E2; non-overlapping holds; bootstrap by day |
| 37 | Every attempt tracked | done | Registry (failures included) shown on Fund control |
| 38 | Simple controls | done | Seeded random-side and momentum-side shadow lots for every AI lot, scored the same way |
| 39 | Attribution | partial | By horizon, side, label, support, memory; execution slippage separately. Regime is carried on each lot but not yet split out in the report. |
| 40 | Constrained self-improvement | done | Hard ceilings in code; no process can loosen a limit or a pass rule |
| 41 | Mode shown prominently | done | Mode bar on Fund control: research / paper orders / simulated / real money never |
| 42 | Buttons say what they do | done | e.g. "Start Autopilot Night (research + Alpaca PAPER orders)"; the old "Auto-trade" labels renamed |
| 43 | Why no trade | done | Concrete reasons per company (Night: no side, already planned, theme rule, or sizing gave 0) and per ETF (Day) |
| 44 | Opportunity funnel | done | Companies → researched → … → evaluated, with drop reasons |
| 45 | Account-risk dashboard | done | Gross, net, beta-net, pending, headroom, ownership, themes, breakers, ceilings |
| 46 | Acknowledged vs filled | done | Order states shown separately |
| 47 | Performance with execution quality | partial | Equity and drawdown on Autopilot, slippage on Fund control. Turnover and spread paid are not shown yet; they need fills, which start at the next open. |
| 48 | Evidence quality | done | Sample, backtest or forward, result for every test; feed check; audit exceptions; fact-check accuracy |
| 49 | Incident timeline | done | What failed, what was exposed, the fallback, the condition to resume, and the resolution |
| 50 | Separate pause, cancel and close | done | Three controls per autopilot; each is confirmed from the broker |

## First result the new report shows

181 AI "day" calls whose exit day has closed: −0.31% average against the market, 43% right, before costs. D13 (forward, after costs): −27.8 bp per call over 38 scored calls. The AI day calls are losing so far. That's consistent with D12's failure and with Night not opening AI day trades any more.
