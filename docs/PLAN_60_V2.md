# Plan v2: targeting 60% a year (paper money)

Written 2026-09-27, 00:30. This replaces the phase list in PLAN_60.md, whose results so far are kept there. Paper
trading only, free data, no real money at any step.

## What tonight's tests changed

| Tested tonight | Result | Lesson |
|---|---|---|
| Trend following, momentum, FX carry | Fail | Textbook sleeves add nothing to SPY + crypto at equal risk |
| Stat-arb, arm A | Fail | Weekly full turnover costs about 20% a year at 10 bps |
| Stat-arb, arm B (8-K filter) | Fail | No stronger edge |
| Long-short 1-week Bonsai book | Fail | Some edge before costs, but the cost wall wins |
| Drawdown brakes | **Pass** | Cut the worst drop from 33.8% to 27.2% at no equal-risk cost |
| Value + quality | Running | Low turnover, so it avoids the cost wall |

**Lesson 1: the cost wall.** Anything replaced weekly pays about 4 × cost × 52 a year. That is 20% a year at 10 bps,
before any edge. **Every new signal in this plan must hold 20+ days, or trade only its most extreme scores.**

**Lesson 2: what is left.** The only book with a pre-cost *and* post-cost edge is the one already built:

- SPY core + crypto sleeve (B0), Sharpe 1.05 over 2018–26.
- Plus Bonsai's long-only 1-week satellite. The combined book's Sharpe is 1.24, but that comes from only about 2.5
  years.

## What 60% needs from that book

Risk-free rate 4%; figures from `scripts/target66.py`.

| If the combined book's true Sharpe is | Volatility needed for 60% | Leverage on a ~21% vol book | Typical worst drop (10-year median) |
|---|---|---|---|
| 1.24 (tonight's figure) | 42% | about 2× | ~50% |
| 1.05 (B0 alone) | 56% | about 2.7× | ~64% |
| 0.87 (70% of 1.24, the usual haircut) | **cannot reach 60% at any leverage** (best possible ≈ 52%) | — | — |

**So the whole plan comes down to one question:** is the combined Sharpe really about 1.25 or higher? Everything
below either raises that number with low-turnover signals, or measures it honestly before any leverage is used.

## The plan

### Stage 1 (weeks 1–2): raise the Sharpe with low-turnover signals

Every item gets its own spec before its run, and passes only under the adding rule (equal-risk CAGR up; the 90% CI of
the Sharpe difference above 0).

1. **Value + quality sleeve.** Running now. Monthly rebalance, turnover about 3× a year.
2. **The 20-day Bonsai book as a satellite.**
   - 4× less turnover than the 1-week book.
   - The 1-month IC is +0.094 in 2025-26 and +0.027 in 2024.
   - Uses the same master-portfolio code with `--horizon 20`, at 10 and 25 bps.
3. **Research v3 briefs** (tonight's run). If a book passes its rule, researched fact sheets feed item 2.
4. **Extreme-only breadth:** S&P 400/600 earnings releases, trading only |z| > 1.5 of Bonsai's score (about 13% of
   releases).
   - Uses Bonsai-lite triage to save GPU, re-tested on this universe first.
   - A GPU job of several nights.
5. **Crypto sleeve cap: needs your OK first.**
   - Crypto has the highest-return, lowest-correlation asset in the book. Raising the cap from 20% to 35% adds return
     *without borrowing*, but adds crypto's drawdowns.
   - Test: cap 35% vs 20%, 2018–26, under the adding rule. It is one trial, not a sweep.

### Stage 2 (weeks 2–4): build it to trade

1. **Brakes** (passed): −10% from peak cuts risk to ⅔, −20% to ½.
2. **Spike check** (analyze before responding): Jan + Bonsai explain any 4-sigma move before the book reacts. Hard
   limits never wait for the analysis.
3. **Leverage through futures, not margin.** Micro E-mini S&P (MES) for the SPY core, and CME micro bitcoin for crypto,
   both in the simulator. Financing is built into futures prices, which is cheaper than retail margin at about
   rf + 1.5%.
4. **PC-off fallback.** On days the PC is off, Bonsai-lite (CPU, milliseconds) makes the event decisions. Each such day
   is flagged, and scored separately.

### Stage 3 (months 1–6): forward paper test at 1.0×

- The combined book at about 20% volatility, with the brakes on.
- Monthly report: realized Sharpe with its CI, slippage against the backtest's fills, failure-log review.

### Stage 4 (month 6 on): leverage only as the evidence allows

| Forward Sharpe, lower 80% bound | Volatility target | Expected a year | Typical worst drop |
|---|---|---|---|
| < 0.6 | 20% (1.0×) | 12–18% | ~25% |
| 0.6–0.9 | 25–30% | 20–30% | ~35% |
| 0.9–1.2 | 35% | 35–45% | ~40% |
| **≥ 1.25 for 12+ months** | **42%** | **~60%** | **~45–50%** (brakes on) |

- Re-checked monthly. When the bound falls, leverage comes down the same month.
- A −25% drop from peak halves the target until a new high.

## Odds, stated plainly

- **60% needs** the combined book's *true* Sharpe to be about 1.25 or higher, and you to accept drops of about half
  the account.
- **The history is against it:**
  - Most backtested Sharpes shrink 50–75% in live trading.
  - Every textbook sleeve tested tonight failed.
- **The realistic central outcome** is a forward Sharpe of about 0.7–1.0, which gives **20–35% a year** at 25–35%
  volatility. That is still well above SPY's long-run 10%.
- **60% is reachable only in the top row of the Stage 4 table,** and only the forward test can tell us whether we are
  in it. The plan is built so that if we are not, it lands on the best return the evidence supports, instead of
  borrowing into a blow-up.

## Decisions needed from you

1. **Crypto cap test** (Stage 1 item 5): may the test consider a 35% crypto cap? The current rule is 20%.
2. **Drawdown you would sit through at the 60% setting:** about 45–50%. If that is too much, the plan tops out at the
   row you can live with.

## Timeline

| When | Output |
|---|---|
| Tonight | Value + quality result; research v3 scored; push; shutdown (as commanded) |
| Week 1 | 20-day satellite test; crypto cap test (if OK'd); spike check built |
| Week 2 | Extreme-only breadth test starts (GPU nights); futures leverage in the simulator |
| Week 4 | Forward paper test at 1.0× starts, brakes on |
| Month 6 | First leverage decision (Stage 4 table) |
| Month 12 | Whether the 60% row is reachable |
