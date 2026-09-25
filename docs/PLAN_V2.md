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
