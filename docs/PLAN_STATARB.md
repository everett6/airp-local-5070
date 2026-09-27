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

## Result: arm A (2026-09-27, 00:00), FAIL on every rule

- **Dev window (2010–17, bug check).**
  - Sharpe before costs: 0.07.
  - Independent check: a raw 5-day reversal rank IC of 0.009 (t = 1.2), computed without the residual code. That
    rules out a sign or timing bug.
- **Holdout (2018–26-09, opened once).**
  - Turnover is 0.8× a day and average gross 2.1×, which is inherent to 5-day holding.

  | Cost | CAGR | Sharpe | Max DD |
  |---|---|---|---|
  | 5 bps | −6.5% | −0.57 | 53% |
  | 10 bps | −15.5% | −1.50 (95% CI −2.22 to −0.76) | 79.6% |
  | 25 bps | −37.7% | −4.26 | 98% |

  - DSR is 0.0, with N = 7 trials.
  - Added to B0, it drops the equal-risk CAGR from 21.8% to 2.6%.
- **Why:** before costs the book earns only about +3–4% a year. At 10 bps, costs are about 20% a year. The literature
  was right: in the 100 most liquid names, weekly reversal is too small to pay for weekly turnover.

**What this means for arm B.** B keeps A's turnover per unit of gross, so it pays the same ~1.8 Sharpe of cost drag
at 10 bps. It passes only if the no-news subset has **about 5–6× A's gross edge**. That is possible in principle (Chan
2003 finds no-news reversal much stronger than the average), but unlikely in mega-caps.

**The labelling job is large:**

- 11,580 candidate entries in 2024-06 → 2026-09, which is 3,807 distinct episodes over 126 names.
- Each episode needs an 8-K check plus archived-news lookups, which take up to 150 s each on the Wayback Machine.
- That is **days of network time**, not one night.

**Staged plan for B.** A stage that shows no edge ends B, and it is recorded as failed.

1. **Cheap pre-screen** (CPU and EDGAR only, a new trial): drop episodes with an 8-K in the window. If the remaining
   episodes' gross edge (before costs) is not at least 2× A's, B stops here.
2. **If stage 1 passes:** Jan labels a random 500-episode sample. Measure the "unknown" share and the no-news gross
   edge. If the unknown share is above 50%, or the edge is below 5× A's, B stops.
3. **If stage 2 passes:** label all 3,807 episodes over several nights, then apply B's pass rule.

### Arm B, stage 1 spec (written 2026-09-27 before its run)

- **Data:** each candidate's 8-K and 8-K/A filings, from SEC EDGAR's submissions API (free; the SEC user agent from
  backend/.env), using the acceptance date.
- **Filter:** a candidate formed at close t is **dropped** if an 8-K was accepted on any trading day in t−4..t. The
  remaining names on that side keep the side's total weight, split equally. Dropped names are *not* replaced by the
  next-ranked names.
- **Everything else is exactly arm A:** hedges, overlapping 5-day books, t+1 open entry, volatility scaling.
- **Window:** 2024-06-01 → 2026-09, the same days for both books.
- **Measure:** gross edge = annualized mean return at 0 bps ÷ average gross exposure. Computed for the filtered book
  (B1) and for arm A.
- **Continue to stage 2 only if both hold:** B1's gross edge > 0, **and** it is ≥ 2× arm A's gross edge. Otherwise B
  stops and is recorded as failed. The trial is registered either way.

### Arm B, stage 1 result (2026-09-27): B stops here

- **Filter:** 11,580 candidates; 3,604 (31.1%) had an 8-K in their signal window and were dropped. Every name had a
  CIK.
- **Gross edge per unit of gross, before costs, 2024-06 → 2026-09:**

  | Book | Gross edge a year | Sharpe at 0 bps |
  |---|---|---|
  | Arm A | 4.4% | 0.71 |
  | B1 (no-8-K names only) | 4.1% | 0.63 |

  The ratio is **0.93×**, against the required ≥ 2×.
- **Reading:** removing moves that came with an SEC filing does not strengthen the reversal in these mega-caps. The
  no-news idea (Chan 2003) does not show up here.
- Even arm A's pre-cost edge (4.4% a year per unit gross) is far below its ~20% a year cost at 10 bps.
- Stages 2–3, the Jan/Bonsai news labelling, are **not run**. That saves days of GPU and network time. The trial is
  registered (`results/trials_registry.jsonl`, now N = 9).
- **What would reopen it:** a new spec with a cheaper cost structure. Examples: holding periods of 20+ days, or
  trading only the extreme tail, a few names a week. It would count as a new trial.
