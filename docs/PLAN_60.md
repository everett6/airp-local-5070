# Plan: the path to ~60% a year (paper money)

Written 2026-09-26, 23:55, at the user's request. Paper trading only; free data; no real money is involved at any
step. Background and sources: docs/STRATEGY_RESEARCH.md.

## The honest starting point

A return of 60% a year is not something we can choose. It is what you get if **two numbers** come out right:

- the book's real Sharpe ratio (return per unit of risk, after costs), and
- how much risk (volatility) we are willing to run.

Leverage turns the second into return, but only if the first is real.

### What 60% requires

Risk-free rate taken as 4%.

| Volatility we run | Sharpe needed |
|---|---|
| 20% | 2.25 |
| 30% | 1.58 |
| 40% | 1.28 |
| 56% | 1.05 |

Below a Sharpe of about **0.93**, no amount of leverage ever reaches 60%.

### The price in drawdowns

Monte Carlo, 10 years, fat tails. "True Sharpe" is what the market actually delivers after we choose the leverage
from the backtest Sharpe.

| Setting | Backtest Sharpe holds | True Sharpe is 70% of it | True Sharpe is half of it |
|---|---|---|---|
| Sharpe 1.58 at 30% vol | 60% a year, typical worst drop 33% | 39%, drop 38% | 26%, drop 43% |
| Sharpe 1.28 at 40% vol | 60%, drop 46% | 37%, drop 53% | 24%, drop 59% |
| Sharpe 1.05 at 56% vol (today's SPY + crypto book) | 60%, drop 64% | 34%, drop 71% | 20%, drop 77% |

### Where we are now

| What | Sharpe | Over | Caveats |
|---|---|---|---|
| SPY + crypto book | 1.05 | 2018–26 | A strong bull market; no evidence it holds |
| Bonsai 1-week book | 1.24 | ~2.5 years | 95% range about −0.4 to 2.9 |

Four textbook sleeves (trend, momentum, FX carry) and the Bonsai-lite copy all failed tonight's tests.

**Conclusion:**

- Levering today's book to 60% means running about 56% volatility with a typical worst drop near two-thirds.
- If the Sharpe is overstated even a little, the leverage does more harm than good.
- So the plan does **not** lever up first. It works on the only thing that can make 60% survivable: a higher, proven
  Sharpe. Leverage comes last, and only as far as the forward evidence allows.

## The one lever with room to grow: breadth

The "fundamental law of active management" (Grinold & Kahn) says the Sharpe ≈ IC × √(number of independent bets a
year).

- **Today:** Bonsai's 1-week IC is about 0.10, but it acts only on S&P 500 earnings releases, long only, with a few
  hundred usable bets a year.
- **If the IC held** across about 3,000–6,000 releases a year (S&P 1500 / Russell 1000), and the book were
  long-short: the theoretical Sharpe rises well above 1.5.
- **What the literature warns** (STRATEGY_RESEARCH.md, Martineau; Chordia et al.; Lopez-Lira & Tang):
  - The IC is usually lower in smaller stocks after costs.
  - 1-week holding makes costs decisive.
- So breadth is a hypothesis to test, not a promise.

## Phases

Every step has a rule written before its run. A failed step is recorded, not dropped.

### Phase 1 (weeks 1–3): raise the Sharpe with more, cheaper, better bets

1. **Tonight's research v3 result.** If a book passes its rule, researched fact sheets become the default for that
   book.
2. **Long-short 1-week event book.** Buy Bonsai's top fifth and short its bottom fifth each week, market-neutral. The
   simulator allows shorting.
   - Test on 2024 and 2025-26 at 10 and 25 bps.
   - **Pass:** the net Sharpe's 95% CI is above 0 in *both* years.
   - **Spec (fixed 2026-09-27 before the run; `scripts/longshort_event_book.py`):**
     - Releases are grouped by the calendar week of their entry day.
     - Weeks with fewer than 10 releases stay in cash.
     - Within a week, Bonsai's 1-week log-odds ranks the releases: long the top fifth, short the bottom fifth, equal
       weight, 1 unit per side.
     - Each position runs from the entry-day open to the open 5 trading days later.
     - **Primary:** raw stock returns, dollar-neutral, with 4 × cost per week (in and out, both sides).
     - **Secondary, reported only:** the sector-ETF-hedged version (the IC's own outcome), with 8 × cost.
     - Weekly returns, cash weeks included, annualized by √52.
     - The Sharpe CI is a 13-week circular block bootstrap with 2,000 resamples.
     - Both years use the SEC-cross-checked fact-sheet decisions (`factsheet2024_secchk_h5`,
       `factsheet_secchk_h5`).
   - **Result (2026-09-27, 00:09): FAIL.** Output: `results/events/longshort_event_book.json`.

     | Version | 2024 Sharpe | 2025-26 Sharpe |
     |---|---|---|
     | Raw, 0 bps | 0.88 (CI −0.64 to 2.06) | 0.51 (CI −1.18 to 2.34) |
     | Raw, 10 bps | 0.05 (CI −1.49 to 1.23) | 0.15 (CI −1.55 to 2.04) |
     | Raw, 25 bps | −1.20 | −0.40 |
     | Hedged, 0 bps | 1.56 (CI 0.46 to 2.88) | 0.40 |
     | Hedged, 10 bps | −0.43 | −0.41 |

     - 2024 traded 32 of 52 weeks; the 2025-26 sample traded only 30 of 90 weeks (thin).
     - **Reading:** Bonsai's ranking has some pre-cost edge, but replacing both legs every week costs about 20–40% a
       year at 10 bps, and that eats all of it.
     - **Same lesson as stat-arb:** anything held 1 week and fully replaced pays a cost wall of roughly 4 × bps × 52.
       Item 3 (breadth) is therefore re-specified *before* its run. It must either hold longer (the 20-day book), or
       trade only the most extreme scores, so that turnover per unit of edge falls.
3. **Breadth: S&P 400/600 earnings releases** (free EDGAR + Yahoo).
   - Same fact sheets and same Bonsai prompt.
   - A2-style bias control: the universe comes from dated membership, and delisted names are kept where data exists.
   - **Pass:** the IC's CI is above 0, *and* the IC is still positive after 25 bps costs.
   - **GPU budget:** triage (A1) halves the calls, if it is re-tested and passes on this universe.
4. **Daily statistical arbitrage** (the "slow Medallion" test): residual short-term reversal on the top-100
   names, with a no-news variant that uses Jan and Bonsai. Full spec and pass rules: docs/PLAN_STATARB.md.
5. **Combine what passes** with SPY + crypto, using the adding rule (equal-risk CAGR up; Sharpe-difference CI above
   0).

### Phase 2 (weeks 2–4, in parallel): protect the downside

1. **Spike check** (analyze-before-responding), as specified in STRATEGY_RESEARCH.md.
2. **Book-level brakes.** At −10% from peak, cut risk by a third; at −20%, cut it in half.
   - Test as a rule on 2018–26.
   - **Pass:** the worst drop gets smaller *and* the equal-risk CAGR doesn't fall by more than 1 point.
   - **Spec (fixed 2026-09-27 before the run; `scripts/drawdown_brakes.py`):**
     - Applied to B0's daily returns, 2018-01-02 → 2026-09-24.
     - Exposure multiplier for day d, set from the **braked** book's own drawdown at the close of d−1:
       ≥ 20% → 0.5; ≥ 10% → 2/3; otherwise 1.
     - The cut part sits in cash earning 0 (conservative).
     - Cost: 10 bps × |change in multiplier|.
     - Equal risk: the braked book is scaled to B0's realized volatility before comparing CAGR.
   - **Result (2026-09-27, 00:15): PASS.** Output: `results/drawdown_brakes.json`.

     | Book | CAGR | Vol | Sharpe | Max DD | Worst year |
     |---|---|---|---|---|---|
     | B0 | 21.6% | 20.6% | 1.05 | 33.8% | −23.0% |
     | Braked (on 38% of days) | 17.7% | 16.6% | 1.07 | 27.2% | −19.7% |
     | Braked, scaled to B0's vol | 21.9% | 20.6% | 1.07 | 32.9% | — |

     - **Reading:** the brakes mostly *lower risk*. The Sharpe barely moves, so they don't add edge, but they cut the
       worst drop by 6.6 points at almost no cost in equal-risk return. That fits the literature on risk overlays.
     - **Adopted** as a standing rule for the Phase 3 forward test and for any levered book in Phase 4. There it is the
       cheap part of the drawdown protection.
3. **Value + quality sleeve** from EDGAR XBRL point-in-time fundamentals (2009+). Its spec is written before its run.

### Phase 3 (months 1–6): forward paper test with no leverage

- The combined book runs in the simulator at 1.0× gross, **~20% volatility**, orders at the planned times.
- **Monthly report:** realized Sharpe with its CI, slippage vs the backtest's fills, and failures (the failure log).
- **Stop** a sleeve if its forward results fall more than 2 standard errors below its backtest.

### Phase 4 (month 6 on): leverage only as far as the evidence allows

The volatility target is set from the forward record:

> volatility target = half-Kelly on the **lower 80% bound** of the forward Sharpe, capped at 40%.

| Forward Sharpe lower bound | Volatility target | Expected a year |
|---|---|---|
| ≤ 0.5 | 20% (stay unlevered) | ~12–15% |
| 0.8 | 30% (capped by the drawdown rule below) | ~25–30% |
| 1.1 | 40% | ~40–45% |
| ≥ 1.3, **12+ months** forward, and backtest agreement | 40% | ~55–60%, typical worst drop ~45% |

- The table is re-checked every month. When the lower bound falls, leverage comes down the same month.
- **The drawdown rule overrides everything:** a −25% drop from peak halves the volatility target until a new high.

## What would make this plan give up on 60%

- **After 12 forward months,** the lower 80% bound of the combined Sharpe is below 0.9. Then 60% is out of reach at
  any leverage. The target drops to what the bound supports (the table above), and the book keeps running.
- **The event book's IC** fails in both the long-short and the breadth tests. Bonsai then stays a long-only 1-week
  sleeve, and 60% has no candidate left.
- **Costs above 25 bps** erase the 1-week edge.

## Timeline (targets, not promises)

| When | Output |
|---|---|
| This week | Phase 1 items 1–2; spike check built |
| Weeks 2–3 | Breadth test (item 3); brakes; combined book (item 4) |
| Week 4 | Forward paper test starts at 1.0× |
| Month 6 | First leverage decision under the Phase 4 table |
| Month 12 | The real answer to "can this make 60%?" |
