# Autopilot Night and Autopilot Day (7 Oct 2026)

Both autopilots trade the **separate autopilot paper account**. Each has its own button on the app's Autopilot page and runs as its own process. They never touch each other's stocks.

| | **Autopilot Night** (`full_auto`) | **Autopilot Day** (`autopilot_day`) |
|---|---|---|
| What | Jan/Bonsai research, plus the AI long/short swing book (5, 21, 63 sessions) | Price-only day trading of 14 ETFs (E1 engine, no AI) |
| When | Always. It researches between scheduled jobs and trades while the market is open. | Only inside your window (default 09:35–15:55 New York); flat at its end |
| Leverage | Gross 2.0 with the beta hedge (unchanged) | 3.0x for the algo book, 3.9x for the whole account (unchanged) |
| Code | `backend/scripts/full_auto.py` | `backend/scripts/algo_engine.py`; window in `backend/config/autopilot_day.json` |

**Risk rules (leverage not reduced; docs/PLAN_60_V2.md "R1-R3"):**
- **R1, theme caps (Night):** each theme (AI tech, nuclear, quantum) can be at most 30% net and 50% gross of equity. The weight removed goes to the other stocks, so total leverage stays the same.
- **R2, circuit breaker:**
  - Account down 4% on the day: Night sends only reducing orders, and Day opens nothing new.
  - Account down 5%: Day goes flat.
  - The existing limits stay: 15% daily-loss check, 35% drawdown kill.
- **R3, stale data (Day):**
  - A bar that arrives more than 3 minutes late is not traded.
  - No bars for 10 minutes inside the window: flat.
- **Stopping Day** closes its ETF positions, so nothing is held overnight.

**Research memory, M1** (`backend/app/sandbox/research_memory.py`; the "plugin" for Jan and Bonsai). Saved on the SSD under `backend/results/research_memory/<TICKER>.json`:
- **Saved per company:** the checked facts (with dates and sources), every page already read, and past calls.
- **Jan:**
  - Skips pages it has read before.
  - Reuses the saved facts when nothing new turned up.
  - Is told what is already known and asked to look for what is new.
  - Gets a short note on its area (recent facts about theme or sector peers).
- **Bonsai:** gets the saved facts on its card, plus the dates and sides of its earlier calls. Its old bull/bear text is left out, so it can't quote its own past opinion as evidence.
- **Time budget:** 8 minutes a company the first time, 5 minutes once memory exists (was 10).
- **Accuracy:** not proven. The memory makes research faster and keeps more context. Whether calls get more accurate will show in the D13 forward record, which reports memory and non-memory calls separately.

**App:**
- The Autopilot page has the two buttons, with live progress bars:
  - Night: research freshness and the current research batch against its time budget.
  - Day: progress through the trading window, plus decisions, mode, signal speed and risk state.
- Day's hours are set on the Autopilot page.
- The Run Center shows a live bar for each running job.
