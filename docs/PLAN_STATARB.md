# Plan: daily statistical arbitrage (the "slow Medallion" test)

Written 2026-09-27 at the user's request. Paper money, free daily data, no GPU for the main arm. The spec and pass rules
below are fixed **before** any code runs; the parameters come from the literature, not from tuning. It is part of
PLAN_60.md, Phase 1.

## Idea and evidence

When a big stock moves sharply against its sector for a few days *without news*, part of that move tends to reverse
within about a week. Liquidity providers get paid for absorbing order imbalances.

**For:**

- **Lehmann (1990, QJE) and Jegadeesh (1990, JF)** [PR]: weekly and monthly reversal in US stocks.
- **Avellaneda & Lee, "Statistical arbitrage in the US equities market", Quantitative Finance 10(7), 2010** [PR]:
  trade the residual after removing the sector/ETF factor, and hold for days.
- **Chan, "Stock price reaction to news and no-news", JFE 70(2), 2003** [PR]: moves *without* headlines reverse, while
  moves *with* news drift on. That gives Bonsai/Jan a role: telling the two apart.

**Against:**

- In AQR's live data, short-term reversal is the strategy most limited by trading costs (Frazzini, Israel, Moskowitz).
- Profits have shrunk since the 2000s (Avellaneda & Lee; Khandani & Lo 2011 on the August 2007 quant crash)
  *(sizes not re-checked)*.
- Our universe is the 100 most liquid names. That keeps costs low, but it is also where the effect is weakest.

**Prior:** more likely to fail than pass. It is worth one clean test because it needs no paid data and is nearly
uncorrelated with SPY + crypto.

## Arm A: residual reversal (no AI)

- **Universe:** each year's point-in-time top-100 S&P 500 names (`data/hist`, 2010–26).
- **Sector hedges:** the SPDR sector ETFs, with SPY used where the sector ETF did not yet exist (XLRE before 2015-10,
  XLC before 2018-06).
- **Residual** for stock i on day t:
  - r_i − β_i · r_sector.
  - β_i comes from a 60-day regression that ends at t−1.
- **Signal** at the close of t:
  - s_i = −(sum of residuals over t−4..t) ÷ (60-day residual volatility).
  - Standardized across stocks each day.
- **Portfolio:**
  - Long the 10 names with the highest s (the biggest residual losers), short the 10 lowest. Equal weight, dollar
    neutral.
  - Hedged back to the sectors with the same betas, so the book is sector-neutral.
- **Holding:**
  - Five overlapping daily portfolios (Jegadeesh–Titman). Each is held 5 trading days and carries 1/5 of the capital.
  - Turnover is about one fifth of the book a day.
- **Execution:**
  - The signal is computed at close t, and orders fill at the **t+1 open**. This avoids bid-ask bounce and stays
    look-ahead-free.
  - Returns run open-to-open.
- **Risk:** the book is scaled to 10% ex-ante volatility (126-day realized), capped at 3× gross. Borrow costs 0.5% a
  year on shorts.
- **Costs:** 5, 10 and 25 bps per side. **10 bps is the base.**

## Arm B: reversal only for "no-news" moves (uses Jan + Bonsai)

- Same as arm A, but a name enters only if Jan's as-of search finds **no** company-specific news or filing in the
  signal window.
- The spike-check brief (STRATEGY_RESEARCH.md) is reused, with the label "unexplained".
- Moves with news are skipped; per Chan (2003), those may drift instead.
- Runs only on **2024-06 → 2026-09**, because the as-of news tools do not reach further back.
- GPU cost: about 20 candidates a day × about 3 s for Jan, plus Bonsai briefs only when news is found.

**User's choice (2026-09-27): version B is the target.** Arm A is still built first, because B is A plus a filter,
and B is judged against A on the same days.

**Coverage rule for B**, fixed before any run:

- `news_as_of` reads archived pages (Wayback). A missing archive is **not** "no news".
- Each candidate is labelled from the as-of tools over the signal window (t−4..t):
  - **news:** an 8-K was accepted in the window (`sec_filings_as_of`), or an archived headline dated in the window
    names the company.
  - **no news:** there is an archive snapshot inside the window, and it has no such headline and no 8-K.
  - **unknown:** there is no archive snapshot in the window and no 8-K.
- **B trades only "no news" names.** "Unknown" names are excluded from B, and their share is reported.
- If more than 50% of candidates are "unknown", B cannot be judged. It is then recorded as untestable with free
  archives, not as passed or failed.

## Windows (opened once each, in this order)

1. **2010–2017: bug check only.** Look for look-ahead, turnover and cost accounting errors. No parameter may change
   after seeing its Sharpe; if one does, it counts as a new trial.
2. **2018–2026-09: the holdout.** Opened once, after the code passes the bug check and is committed.
3. **Arm B vs arm A** on the same days, 2024-06 → 2026-09.

## Pass rules

**Arm A is adopted as a sleeve only if all of these hold:**

1. On the holdout at 10 bps, the net Sharpe's 95% block-bootstrap CI is above 0 (3-month blocks, 2,000 resamples).
2. At 25 bps, the net Sharpe is still above 0.
3. The Deflated Sharpe Ratio is above 0.95, counting every trial in `results/trials_registry.jsonl`. That is at least
   5 so far tonight (trend, momentum, FX carry, lite, triage) plus this one.
4. **Adding rule vs B0 (SPY + crypto), 2018–26:**
   - The equal-risk CAGR goes up.
   - The 90% CI of the Sharpe difference lies above 0.

**Arm B replaces arm A only if** its net Sharpe beats A's on the same days *and* the paired bootstrap 90% CI of the
difference lies above 0.

## If it passes

- It runs in the paper simulator at **1.0× gross**, with market-on-open orders, for at least 3 months.
- Slippage is measured against the backtest's opens.
- It then joins PLAN_60 Phase 4's leverage table like any other sleeve.

## If it fails

It is recorded as failed, with its numbers, in STRATEGY_RESEARCH.md. Extensions such as pairs or cointegration within
a sector, or a wider universe, need their own new spec and count as new trials.

## Build list and time

| # | Step | Time |
|---|---|---|
| 1 | Fetch the sector ETFs (free Yahoo, with retry and a no-partial-universe check) | 10 min |
| 2 | `scripts/statarb_sleeve.py`: vectorized residuals (rolling β as matrix ops), signals, overlapping books, open-to-open returns, costs | 1–2 h, CPU |
| 3 | `results/trials_registry.jsonl` + Deflated Sharpe Ratio function, with tests | 1 h |
| 4 | Bug-check window (2010–17), then commit | 30 min |
| 5 | Holdout run, then results in STRATEGY_RESEARCH.md, commit, push | 10 min |
| 6 | Arm B (after the spike check is built) | GPU, about 1 night |
