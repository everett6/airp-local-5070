# Executive summary: can a local LLM pick stocks? (status 2026-09-27)

Research project, paper money only, free data only (SEC EDGAR, Wikipedia, Yahoo, FRED, the Internet Archive).
Everything runs on one RTX 5070 (12 GB). Nothing here is investment advice.

## Bottom line

1. **The portfolio that works is simple:** the S&P 500 (SPY) plus a bitcoin/ether trend sleeve capped at 20%,
   with drawdown brakes.
   - Without brakes, 2018–26: **21.6% a year**, Sharpe 1.05, worst drop 33.8%. SPY alone made 14.5% with Sharpe 0.81.
   - With the brakes, the worst drop falls to 27.2%. At equal risk the brakes add 0.3 points a year.
   - Raw return with the brakes is lower (17.7%) because they cut exposure after a loss.
   - This is one 8.7-year path, and choosing crypto at all has hindsight.
2. **The AI stock picks are a weak, real signal that does not survive costs.**
   - Bonsai-27B's 1-week score on S&P 500 earnings releases has a rank IC of +0.103 [+0.051, +0.158] in 2025-26,
     but only +0.027 in 2024.
   - Every way of trading it has lost its edge to costs or noise:
     - as a satellite in the book (1-week: a lead, p = 0.06; 20-day: failed tonight);
     - long-short, weekly (failed).
   - It stays in the forward test as a shadow book at 0 weight.
3. **Web research did not help, in three tries.**
   - Research v3 got verified facts on 1,103 of 1,180 releases, and the leak audit found no leaks.
   - Bonsai's IC with the research was no higher in any book: 1 week +0.102 vs +0.103 without.
4. **16 pre-registered strategy trials are in the registry (plus one rerun of a data bug); 1 passed** (the drawdown brakes). Research v3's five
   books also failed.
   - Every result, including the failures, is in `backend/results/trials_registry.jsonl`, which feeds the Deflated
     Sharpe Ratio.
   - **New tonight:** Bonsai's score has no signal on S&P 400/600 releases (IC −0.002 on 6,597 releases), against
     +0.10 on the S&P 500. It may know big companies better, or the S&P 500 result may be partly luck. Only the
     forward test can tell.
   - **The crypto trend rule is sturdy but was lucky:** across 30 parameter pairs the Sharpe is 0.85–1.07. The live
     pair sits in the 87th percentile, so a fair forward expectation for the book is about 19–20% a year, not 21.6%.
5. **30–40% a year needs a Sharpe of about 1.0–1.4 at 20–30% volatility.**
   - The book has a Sharpe of about 1.05 at 20.6% volatility, which is about the low end.
   - Getting more means more risk, through futures leverage (now in the simulator), not a better signal.
   - The forward test runs 1.0×, 1.5× and 2.0× shadow books so the choice can be made on real data.
   - 2× SPY via futures had a 59% drawdown in 2018–26.

## Tests of 26–27 Sep, each with its rule written before the run

| Test | Result | Key number |
|---|---|---|
| Web research v3 (5 books) | fail | 1-week IC +0.102 with research vs +0.103 without |
| 20-day Bonsai satellite | fail | Sharpe +0.01, 90% CI [−0.08, +0.13] |
| Value + quality long-short (XBRL) | fail | 2014–26 Sharpe 0.25, 95% CI [−0.28, +0.80] |
| Volatility target 20% | fail (lead) | Sharpe +0.06, 90% CI [−0.14, +0.25] |
| Crypto cap 35% | fail (lead) | Sharpe +0.04, 90% CI [−0.11, +0.16] |
| Drawdown brakes | **pass** | max DD 33.8% → 27.2%; equal-risk CAGR 21.9% vs 21.6% |
| Long-short 1-week event book | fail | Sharpe 0.05 / 0.15 at 10 bps |
| Stat-arb (reversal), arms A and B | fail / stopped | Sharpe −1.50 after costs |
| Trend, momentum, FX carry sleeves | fail | Sharpe 0.12–0.42 |
| Spike check (analyze before responding) | fail: kept as information only | halving unexplained spikes: −0.19% per trigger [−0.60, +0.24] |
| Breadth: S&P 400/600, 1-week, extremes only | fail | IC −0.002 [−0.044, +0.035] on 6,597 releases |
| Daily crypto trend check | fail (lead) | +1.1 pt a year, Sharpe +0.04 [−0.05, +0.16] |
| skfolio CVaR risk parity instead of the 20% crypto cap | fail (lead) | Sharpe +0.12, 90% CI [−0.01, +0.23] |
| Disagreement: Bonsai vs the earnings-day reaction, 20 days | fail | IC +0.010 [−0.046, +0.066] |
| LLM extracts, code scores: arm A (Bonsai labels from the release only) | fail | adds +0.015 IC over code features [−0.035, +0.058] |
| LLM extracts, code scores: arm B (Jan's web research + Bonsai) | fail | adds +0.007 IC over arm A [−0.037, +0.052] |
| Day trading D1: intraday momentum (SPY+QQQ, 2019–26, after publication) | fail | Sharpe −1.00 [−2.22, −0.08] at 2 bps |
| Day trading D2: 5-minute opening-range breakout (2023–26, after publication) | fail | Sharpe −0.24 [−1.40, +0.62] |
| Day trading D3: noise-area breakout + VWAP stop (2024–26, after publication) | fail | Sharpe +0.20 [−1.24, +1.20]; +0.75 before publication |
| Day trading D4: rest-of-day intraday momentum (2021–26, after publication) | fail | Sharpe −1.21 [−2.13, −0.33] |
| Day trading D5: end-of-day reversal, S&P 500 cross-section (2024-07–2026-09, after publication) | fail | Sharpe −1.86 [−3.65, +0.08]; +0.6 bp/day before costs, 2 bp of costs |
| Day trading D6: "box theory", fade yesterday's range, SPY+QQQ (2016–2026) | fail | Sharpe −0.91 [−1.58, −0.21] (98.3%); ≈0 before costs |
| Day trading D7: intraday periodicity, S&P 500, last half hour (2016–2026) | fail | Sharpe −0.07 [−1.09, +0.74]; +2.0 bp/day before 2 bp of costs |
| Day trading D8: Darvas box breakouts, S&P 500, vs SPY (2024-07–2026-09) | fail | excess Sharpe −0.30 [−1.84, +1.22]; 13.2%/yr vs SPY 18.0% |
| Day trading D9: opening-auction reversal, S&P 500 (2016–2026) | pass, untradable | Sharpe +0.81 [+0.21, +1.34]; Alpaca check +1.08; the signal needs the official open, unknown before the auction |
| Day trading D10: D9 with a 09:25 pre-market signal (tradable) | fail | Sharpe −0.64 [−1.28, −0.06] |
| Day trading D11: D9's selection with limit-on-open orders (tradable) | fail | Sharpe −0.56 [−1.29, +0.09] (98.3%); −0.95 at 2 bps; family closed |
| Pairs trading P1 (Gatev–Goetzmann–Rouwenhorst, within sectors, 2024-07–2026-09) | fail | Sharpe −0.61 [−1.92, +0.91] at 10 bps; −0.24 before costs |
| Crypto funding carry C1 (long spot, short perpetual; 2023-05–2026-09, vs T-bills) | fail | Sharpe −1.63; +4.06 before 2023, below cash since 2025 |
| Turn-of-the-month T1 (SPY, last day + first 3; 2008–2026) | fail | overlay Sharpe +0.30 [−0.10, +0.74]; TOM days only +1.3 bp/day above others, CI [−6.6, +9.4] |
| SPY overnight O1 (close to next open, 2012–2026, 1 bp a side) | fail | net Sharpe +0.25 [−0.28, +0.81]; +0.73 before costs; 2 trades a night eat it |
| Macro-announcement E1 (jobs, PPI, FOMC days; 2013–2026) | fail | net Sharpe +0.08 [−0.42, +0.66]; event days −1.9 bp/day vs other days |
| Rebalancing pressure R1 (SPY vs 7–10y Treasuries; after the paper's sample, 2023–2026) | fail | net Sharpe −0.10 [−1.20, +0.59]; it was +0.90 [+0.52, +1.25] inside the paper's own sample |
| Month-end Treasuries M1 (TLT, last 3 days of the month; 2019–2026) | fail, narrowly (a lead) | net Sharpe +0.51 [−0.10, +1.14]; month-end days +11.0 bp/day vs other days, CI [+1.0, +21.8] |
| Treasury auction cycle A1 (TLT, 5 days after 10y/30y auctions; 2014–2026) | fail | net Sharpe −0.18 [−0.64, +0.27]; the effect was there in 2009–2013 and is gone since |
| Arm B2 (arm B with the news actually gathered) | fail | +0.011 IC over arm A [−0.035, +0.062]; own IC +0.054 [−0.004, +0.114] |
| Arm B3 (code-built evidence: last outlook + real headlines) | fail | +0.001 IC over arm A [−0.032, +0.035]; own IC +0.044 [+0.003, +0.090] |
| Arm C (arm B + Bonsai judgement fields) | fail | −0.022 IC vs arm B [−0.065, +0.025]; `net_read` alone +0.067, to be tested on live data only (C2) |
| 8-K breaking-news watcher (W1) | stopped at quality gate | verified quotes 82.6% (needs 85%); no returns looked at |

## What was built this week

| Piece | What it does |
|---|---|
| Futures in the simulator | MES and micro-bitcoin priced by cost of carry, with daily settlement, margin calls and rolls (8 tests) |
| Forward event runner | New S&P 500 releases → fact sheet → Bonsai (or Bonsai-lite on CPU); hash-chained ledger; on-time rule; never backfilled |
| Forward allocator | SPY + crypto books, now with a braked book |
| Weekly review | Books, shadow 1.5×/2.0× books, event scoreboard, missed runs and decisions |
| Spike check | Triggers above 4σ (3σ for SPY/BTC); Jan gathers evidence as of the close, Bonsai labels the cause, code overrules unsupported labels |
| Trials registry + DSR | Every trial logged; the Deflated Sharpe Ratio uses the real trial count |

## Next steps

1. **Forward test from Mon 5 Oct:**
   - `forward_allocator.py` about weekly;
   - `forward_events.py` twice each weekday, at about 08:45 ET and in the evening. The dry run showed an
     evening-only schedule misses every pre-market release;
   - `weekly_review.py` every Saturday.
2. **After 3 months:** compare forward fills and ICs with the backtest.
3. **Leverage:** it stays at 1.0× (30% volatility cap) until 6 forward months exist and you have given a maximum
   drawdown you can accept.
4. **Options that need your OK:**
   - PrismML's llama.cpp fork, the only real speed-up for Bonsai (a download).
   - A higher crypto cap as the first way to add risk.

## Operational notes

- The forward dry run found two bugs before any real data:
  - a price calendar bug;
  - live fact sheets being dropped because the entry bar does not exist yet.
  Both are fixed; the second dry run decided 6 of 6 releases on time.
- **Name-hiding check of the AI judge (1 Oct 2026, validity test, run once): no evidence of memory.** 3,160 past
  releases re-scored with company, ticker and dates hidden: rank IC +0.069 masked against +0.066 unmasked
  (difference +0.003, 95% interval [−0.008, +0.013]); 5.8% of calls flip. The judge's backtest edge comes from the
  fact sheet, not from recognising the company. It does not make the edge larger; the live book is unchanged.
- **Code review of 1 Oct 2026, before the earnings season** (`docs/CODE_REVIEW_2026-10-01.md`): 24 faults fixed on
  the live path, none of which had cost a decision yet. The most serious: since 29 Sep, one filing without a
  press release (1.4% of past filings, at least one on 12% of release days) would have failed the whole live run
  and every run after it. Others: orders for class shares sent under a symbol the broker does not know, two stale
  ticker symbols (Fiserv, EQR), state files that a power cut could leave half-written, labels that would all have
  counted as late on a busy morning. Most were found by rehearsing each scheduled job on a scratch copy and by
  writing end-to-end tests with a stand-in model, not by reading. 642 tests pass. Six questions are left for the
  user to decide (`docs/open_decisions.json`, shown on the app's Home page).
- (27 Sep) 404 tests pass; ruff and strict mypy are clean; CI is green again (it had been failing on two type errors, now
  fixed). No service or timer runs anything. Every run is manual.
