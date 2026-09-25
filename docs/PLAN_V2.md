# Plan v2 (2026-09-25): follow the one lead, cheaply

What we learned (docs/EXECUTIVE_SUMMARY.md): the LLM's own BUY/PASS judgment is at best weak (IC +0.065, not
significant), but **year-over-year EPS change, read from the press release and checked by code, predicted 20- and
60-day returns vs the sector** (IC +0.15 / +0.20, significant) in a 1,200-event sample. The crypto trend sleeve helped
over 2018-2026. Nothing beat SPY yet. So v2 stops spending GPU hours on LLM opinions and tests the lead properly.

Rules unchanged: free data, paper money, no services, nothing runs on its own.

## Stage A: is the EPS signal real? (CPU + network, ~1 day)

Key point: reading a number is not predicting, so lookahead from the model's training data does not matter here and
the test can use *every* year, not just the 2025+ clean window.

1. **XBRL, no LLM.** SEC's free companyfacts API has reported diluted EPS for every filer since ~2010. Build
   quarterly EPS vs a year earlier for the S&P 1500, dated by the 10-Q/10-K acceptance time (and the 8-K release time
   where the quarters match). Script: `scripts/build_xbrl_eps.py`.
2. **Test over 2010-2026** with the existing event scorer: monthly rank IC and top-minus-bottom fifth, 20 and 60 days,
   by year, by index (500/400/600), and by sector. Pre-registered pass: IC > 0.03 with the 95% interval above zero in
   the full sample **and** positive in at least 2 of 3 sub-periods (2010-15, 2016-20, 2021-26).
3. **Grade the LLM reader against XBRL** on the 1,200 events: agreement rate, and whether the LLM-read version adds
   anything (it is faster: release day vs 10-Q days later).

Kill rule: if XBRL EPS change fails step 2, the stock-picking arm stops here and the project becomes the SPY +
crypto-sleeve allocator only.

## Stage B: make it tradeable (~1 day)

4. Portfolio test in the existing simulator: buy the top fifth at the first open after the release, hold 20 or 60
   days, 5 bps costs + 5 bps slippage, whole shares, $100k, S&P 500 vs 1500 universes, 2010-2026.
5. Master agent score = calibrated logistic of (EPS change, earnings-day reaction, momentum, Bonsai P(BUY) where
   available), walk-forward only. Keep Bonsai only if it adds out-of-sample IC.
6. Compare against SPY and SPY + crypto sleeve: CAGR, Sharpe, max drawdown, turnover, worst year.

## Stage C: LLM where it earns its place (GPU, optional)

7. Finish the remaining 2,450 S&P 500 releases (`LIMIT=0 scripts/event_pipeline.sh`) only if step 3 shows the
   release-day read beats waiting for the 10-Q.
8. Use the LLM for what code can't do: guidance changes and one-off items ("excluding a $2B charge") that make GAAP
   EPS change misleading. Test as an adjustment to the EPS score, not as a BUY/PASS vote.
9. Skip: more BUY/PASS prompting, Ternary-Bonsai-2 (doesn't load in Ollama 0.34), Jan-v1 research agent, until
   something above works.

## Stage D: forward paper test (3 months, manual)

10. One command, run by hand once a week: pull new 8-Ks and XBRL, score, write the paper portfolio and log it with a
    timestamp (committed to git, so results can't be edited after the fact).
11. Gate to keep going after 3 months: forward IC and paper return in line with the backtest's 95% range.

## Order and time

A (1 day) -> B (1 day) -> D starts immediately after B -> C in parallel only if A step 3 justifies it.

## Stage A result (2026-09-25): FAIL, with one open lead

- **XBRL, 2010-2026, S&P 1500 (46,149 quarterly filings, no LLM):** EPS change 20-day monthly rank IC +0.012
  [-0.005, +0.029]; by period +0.049 (2010-15), +0.000 (2016-20), -0.013 (2021-26). Fails the pre-registered rule
  (needed > 0.03 with the interval above zero, positive in 2 of 3 periods). The effect was real a decade ago and is
  gone now. S&P 500 alone: +0.024 [+0.004, +0.044] at 20 days, too small to trade after costs.
- **Release day vs filing day (2024+, 13,476 quarters):** the same XBRL signal entered at the 8-K release scores
  +0.020 (20 days) and +0.046 [+0.011, +0.082] (60 days), vs +0.001 / +0.030 at the 10-Q. Reading on release day
  helps a little.
- **Why last night's sample looked strong:** within the 1,180 LLM-read releases, EPS change scores IC +0.21 only where
  the reader verified BOTH numbers in the release (422 events); where the SEC tool supplied the year-earlier number
  (348 events) it is +0.06, not significant. So the +0.15 came from which releases the reader could verify
  (companies that print the GAAP comparison), 14 months only: a selection effect or a narrow lead, not the broad
  signal the plan hoped for.
- **Reader vs SEC numbers:** this quarter's EPS matches XBRL 87%, the year-earlier 70% (restatements, adjusted EPS).
- **Research table + Bonsai** (scripts/build_features.py: reader + SEC tool + prices -> CSV fact sheet ->
  decide_events.py --features): the SEC tool completed 355 more EPS pairs (781 of 1,180). Bonsai's P(BUY) IC went
  from +0.065 [-0.008, +0.129] to +0.077 [+0.000, +0.153] at 20 days, top-bottom fifth +1.2% -> +2.4%: slightly
  better, still borderline, 14 months.

Per the kill rule, broad EPS-change stock picking stops. The one test left before dropping stock picks entirely:
**out of sample on 2024 releases** (reader on ~1,800 S&P 500 releases, ~1 hour GPU): does "reader verified both
numbers" + EPS change, and Bonsai with the research table, hold up in a year they were not found in? If not, the
project is the SPY + crypto trend allocator.

## Out-of-sample check on 2024 (2026-09-25): both leads FAIL -> stock picking stops

Same reader, SEC tool, research table and Bonsai-27B on all 1,995 S&P 500 earnings releases of 2024
(scripts/oos_eval.py; results/events/oos_2024.json vs oos_2025_26.json):

| Lead | 2025-26 (found) | 2024 (new year) |
|---|---|---|
| L1 EPS change, reader verified both numbers, 20 days | +0.207 [+0.087, +0.332] | +0.043 [-0.021, +0.112] |
| L1 same, 60 days | +0.217 [+0.140, +0.295] | +0.055 [-0.007, +0.122] |
| L2 Bonsai P(BUY) with research table, 20 days | +0.105 [+0.036, +0.171] | +0.020 [-0.053, +0.079] |
| L2 same, 60 days | +0.066 [-0.009, +0.138] | +0.035 [-0.055, +0.115] |

Neither lead survives a year it was not found in, and 2024 is inside Bonsai's training data, which could only have
helped it. The 2025-26 numbers were a small-sample fluke of 12-17 months. Decision: no LLM stock picking, no fine-tuning;
the project continues as the SPY core + BTC/ETH trend-sleeve allocator (the one piece that held over 2018-2026),
next step its forward paper test (Stage D, manual weekly command).
