# Executive summary: can a local LLM pick stocks? (status 2026-09-25)

Research project, paper money only, free data only (SEC EDGAR, Wikipedia, Yahoo, Internet Archive). Everything runs
on one RTX 5070 (12 GB). Nothing here is investment advice.

## Bottom line

1. **No strategy tested so far beats simply holding SPY after costs.** The master portfolio built from Bonsai-27B's
   earnings-release picks returned 17.4% a year (Sharpe 0.99) against SPY's 17.6% (Sharpe 1.06) over
   2025-01-02 to 2026-09-24.
2. **The most promising signal is plain code, not the LLM's judgment:** year-over-year change in diluted EPS, where
   the LLM only *reads* the numbers from the press release and code verifies each one against the text. Its monthly
   rank correlation with the next 20 and 60 days' return vs the sector is +0.15 and +0.20, both with 95% intervals
   above zero, and the top-minus-bottom fifth earns +2.6% / +7.0%. This is from 10-12 months of a random 1,200-event
   sample, so it needs the full sample before anyone relies on it.
3. **Bonsai-27B's own BUY/PASS probability is weakly positive but not proven** (rank correlation +0.065, interval
   -0.008 to +0.129).
4. **The crypto trend sleeve is the only piece that improved a portfolio in a long test:** SPY plus a BTC/ETH trend
   sleeve (at most 20%) returned 21.5% a year with Sharpe 1.05 over 2018-2026, vs SPY's 14.2% / 0.81 and BTC
   buy-and-hold's 21.7% / 0.63 with an 80% drawdown. One 8.7-year path, and choosing crypto at all has hindsight.

## What was built

| Piece | What it does |
|---|---|
| Point-in-time data | S&P 500/400/600 membership from Wikipedia revisions; 16,649 earnings releases (SEC 8-K Item 2.02) 2024-2026 with SEC acceptance times; Yahoo prices |
| Research sub-agent | qwen3:8b with as-of internet tools (Wikipedia revisions, EDGAR, Archive.org captures) that can only see pages that existed on the decision date; writes a fact brief that code verifies |
| Reader | qwen3:8b reads each press release; every number must be quoted word for word from the release and is re-checked by code |
| Decision model | Bonsai-27B (1-bit, 4.6 GB VRAM) answers BUY or PASS; its token probabilities give P(BUY) |
| Master agent | Calibrates each score into P(beats sector) using only outcomes already known, sizes with quarter-Kelly, caps (5% per stock, 25% per sector, 20% crypto), puts the rest in SPY, halves risk in a 15% drawdown |
| Crypto sub-agent | BTC/ETH held while 4-week return > 0 and price above the 100-day average |

## Results in detail

Code-only baselines, all S&P 1500 earnings events, 2025-01 onward (10,239 events): post-earnings drift
(rank correlation -0.034) and momentum (+0.006 at 20 days) show no edge in this period.

Reader race (60 releases): qwen3:8b verified revenue pairs on 25% and EPS pairs on 38% of releases at 2.4 s each;
NuExtract3-4B verified 2% and 3% at 3.3 s. qwen3:8b was used.

S&P 500 releases since 2025, random sample of 1,200 (1,180 decided by Bonsai-27B, 627 BUY, mean P(BUY) 0.51):

| Signal | 20-day rank IC [95% CI] | 60-day rank IC [95% CI] | Top-bottom fifth, 60 days |
|---|---|---|---|
| EPS change (code, from verified numbers) | +0.149 [+0.034, +0.264] | +0.205 [+0.116, +0.296] | +7.0% |
| Bonsai-27B P(BUY) | +0.065 [-0.008, +0.129] | +0.065 [-0.007, +0.134] | +1.1% |
| Earnings-day reaction (drift) | +0.001 | +0.004 | +0.8% |
| Momentum | -0.030 | +0.062 | +1.5% |

Master portfolio (Bonsai picks + crypto sleeve + SPY core), 2025-01-02 to 2026-09-24: +31.8% total, 17.35% a year,
Sharpe 0.99, max drawdown 18.8%, 434 trades; SPY +32.2%, 17.58%, 1.06, 18.3%. By year: 2025 13.1% vs 16.9%,
2026 16.5% vs 13.1%.

## Caveats

- 2025-2026 is only ~20 months; one sample, one market regime.
- LLM stages covered a random 1,200 of 3,652 S&P 500 releases (time budget), so monthly counts are small.
- Readers verify revenue on only a quarter of releases (strict quote check); the EPS result uses the ~38% where a
  pair was verified, which could favour companies with cleaner press releases.
- Ternary-Bonsai-2-27B (PTQ1_0) does not load in Ollama 0.34; only Bonsai-27B was tested.

## Next steps (in order)

1. Finish the remaining 2,450 S&P 500 releases (`LIMIT=0 scripts/event_pipeline.sh`, resumable, about 2 hours) and
   re-score; confirm or kill the EPS-change result.
2. If it holds: make EPS change (plus Bonsai's probability as a second input) the master agent's stock score, and
   re-run the portfolio.
3. Improve reader coverage (fallback to XBRL numbers from SEC's free companyfacts API) and grade the reader against it.
4. Forward paper test from today (manual command, no service) before trusting any backtest.
5. Lock the research agent's internet access with an OpenShell deny-by-default egress policy.

## Operational note

The overnight pipeline stopped for three hours after the baselines because `tee` could not create its output file
in a folder that did not exist yet; `scripts/event_pipeline.sh` now creates it first and takes `LIMIT`, `BENCH` and
`MINCAL` to fit a time budget. 353 tests pass; ruff is clean.
