# Plan from here (written 2026-09-27)

Paper money only, free data only, and every run is started by hand until you switch on autonomy. Every test has its
pass rule committed before it runs and is logged in `backend/results/trials_registry.jsonl`. Details and results are
in [PLAN_60_V2.md](PLAN_60_V2.md); outside ideas in [INSPIRATION.md](INSPIRATION.md).

## Where things stand

- **Book (frozen):** SPY + BTC/ETH trend sleeve (20% cap) + drawdown brakes. Backtest 2018–26: 21.6% a year without
  brakes, 17.7% with them; the realistic forward expectation is about 19–20%.
- **AI picks:** Bonsai's 1-week score has a weak real signal on S&P 500 releases (IC +0.10 in 2025–26) that has not
  survived costs. It is tracked as a shadow book at 0 weight.
- **Built today:**
  - the paper portfolio viewer (Qanat style);
  - the mandate, a fail-closed order gate and the kill switch (HALTED / REDUCING);
  - Binance, Alpaca and Polygon price sources with a Yahoo cross-check;
  - a skfolio stress test.
- **Trials:** 18 registered, 1 pass (the brakes). Leads: the 35% crypto cap, skfolio risk parity, the vol target, the
  daily check.

## Phase 0: tonight and tomorrow morning (GPU, runs on its own)

| When (about) | What | Decides |
|---|---|---|
| ~22:00 | Jan finishes as-of research on 1,995 releases from 2024, then the leak audit | — |
| ~04:00 | Arm B: Bonsai reads the release + Jan's evidence | Does Jan's research add signal? |
| ~05:00 | Arm A: release-only labels finished and scored | Does "LLM extracts, code scores" beat EPS growth? |
| ~11:00 | Arm C: Bonsai with judgement (quality gate first) | Does more judgement add signal beyond arm B? |

**Me:** record each verdict in PLAN_60_V2, the executive summary and the README, then push.

## Phase 1: prep week, Mon 28 Sep – Fri 2 Oct

| Day | Me | You |
|---|---|---|
| Mon | Write up verdicts A/B/C. **Freeze the event pipeline** for the forward test: the best arm that passed, otherwise Bonsai's 1-week score as now | Regenerate the Alpaca keys and put them in `backend/.env` |
| Mon–Tue (CPU) | Pre-register and run **"first reaction"** (the earnings-day move + Bonsai's labels, 20–40 day hold) and **"tilt SPY"** (over/underweight picked stocks inside the SPY holding instead of a separate sleeve) | Pick your **maximum drawdown**. The stress test: a week SPY falls 10% costs the book about 9–15% |
| Wed–Thu (GPU nights) | Build the **8-K breaking-news watcher** (deals, CEO changes, layoffs → Jan → Bonsai → code) and backtest it on 2024–26 under its own rule | Practice the routine: `forward_events.py` at ~08:45 ET and in the evening; open the viewer |
| Fri | Dry run of the full weekday routine; SPY price cross-check against Alpaca; tag the frozen version in git | Decide: PrismML download (Bonsai speed-up), yes or no |

**Rule for the week:** a test that passes joins the forward test as a **shadow** book at 0 weight. Nothing changes the
real book before the 3-month review.

## Phase 2: forward test, Mon 5 Oct – early Jan 2027 (about 13 weeks)

| Cadence | Command | Who |
|---|---|---|
| Every weekday, ~08:45 ET and evening | `python scripts/forward_events.py` | you (or autonomy, once on) |
| Weekly (Mondays) | `python scripts/forward_allocator.py` | you |
| Saturdays | `python scripts/weekly_review.py` and `python scripts/failure_review.py` | you, then I read them |
| Any time | `scripts/portfolio_ui.sh --lan` (phone too) | you |
| Emergency | `forward_allocator.py --halt "why"` or `--reduce "why"`; `--resume` to undo | you |

- **Monthly (me):** a report with the forward Sharpe and its CI, fills vs the backtest, each shadow book's IC, and
  missed runs and failures.
- **Autonomy (optional, from week 3):** once two weeks of manual runs are clean and you say "switch it on": timers
  with wake-from-sleep, a heartbeat and phone alerts. The kill switch and mandate are already in place.

## Phase 3: 3-month review, early Jan 2027

The pass/fail rules for this review are written before it, by the end of October:

1. **Implementation check:** fills and slippage within what the backtest assumed, no rule broken, and the forward IC
   within 2 standard errors of the backtest.
2. **AI picks:** if the event book's IC holds up (lower bound above 0 on backtest + forward data combined), it gets a
   small real weight (10–20%) through a pre-registered sizing rule and a mandate change that you commit.
3. **Leverage:** stays at 1.0× (at most 30% volatility) until 6 forward months exist and you have set a maximum
   drawdown.

## Phase 4: month 6 on, about Apr 2027

- The Stage 4 table in PLAN_60_V2 sets volatility and leverage from the forward Sharpe's lower bound.
- **Realistic range:** 20–30% a year. 60% only if the forward Sharpe holds at 1.25 or more for 12+ months and you
  accept drops of about half the account.
- **Queued for then** (each needs a new test):
  - the crypto-cap leads (35% cap, skfolio risk parity);
  - Bonsai asked several times and averaged (needs PrismML);
  - a bounded RD-Agent-style signal search: a fixed budget, a held-back period, every candidate registered.

## Decisions only you can make

| Decision | Needed by | Why |
|---|---|---|
| Maximum drawdown you would sit through | Fri 2 Oct (for alerts); month 6 (for leverage) | Sets the brake alerts and caps any leverage |
| Alpaca keys in `backend/.env` | Fri 2 Oct | The SPY cross-check and a price fallback |
| The PrismML llama.cpp fork download | any time | The only real speed-up for Bonsai; enables averaging several answers |
| Switching on autonomy | after 2 clean weeks | Runs keep going when you are busy |
| Mandate changes (crypto cap, AI picks weight) | only after a passed test | The mandate is yours; code never changes it |

## Standing rules

- Paper money only; no paid data or subscriptions.
- Never shut the PC down unless you say so.
- Research uses Jan and Bonsai. Stock picking is never dropped without your say.
- Failed tests are not re-proposed as new ideas; leads wait for a new pre-registered test.
