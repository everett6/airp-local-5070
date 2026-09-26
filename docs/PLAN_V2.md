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

## Next (2026-09-25): research sub-agent swap test and Stage D

**Jan-v1-4B as the research sub-agent** (the model that searches the as-of internet tools and writes the checked
brief). Jan-v1 is a Qwen3-4B fine-tune for agentic web research (janhq/Jan-v1-4B-GGUF). Test: the same research tasks
as the qwen3:8b analyst run (every 4th month of 2025-01..2026-08, top-20 screen, 100 tasks), same tools and web cache,
`scripts/compare_research.py analyst analyst_jan`. Rule fixed before the run: Jan replaces qwen3:8b only with at least
as many source-verified facts per brief AND >= 1.5x faster (it is half the size, so it also frees ~2.5 GB of VRAM).
This measures research quality, not returns: per the stock-picking verdict, briefs are no longer used to pick stocks,
so the research agent only matters for future pre-registered hypotheses and for the allocator's review step.

**Stage D: forward paper test of the allocator** (the part that held up 2018-2026).
- `python scripts/forward_allocator.py`, by hand about once a week; `--status` prints the ledger. No service, no timer.
- Books: master (SPY core + BTC/ETH trend sleeve, crypto <= 20%), SPY buy-and-hold, fixed 80/20 SPY/BTC.
- Orders decided at a run fill at the first open after the run's UTC date; today's bar is never used; 5 bps costs;
  whole SPY shares, fractional crypto (app/portfolio/forward.py, tests/test_forward_allocator.py).
- Each run appends to results/forward/allocator/ledger.jsonl and commits it to git itself, so results can't be
  edited afterwards.
- Started 2026-09-25 (first targets: SPY 78%, BTC 11.6%, ETH 8.4%). Gate on 2026-12-25: the allocator's forward
  return, volatility and drawdown vs SPY and 80/20 are within the range of 13-week windows in the 2018-2026 backtest;
  if not, find out why before trusting the backtest.

### Jan-v1-4B result (2026-09-25): FAIL, keep qwen3:8b

Same 100 research tasks, same tools and web cache, 1-slot server (results/compare_analyst_vs_analyst_jan.json):

| | qwen3:8b | Jan-v1-4B |
|---|---|---|
| source-verified facts per brief | 4.47 | 0.36 |
| tasks with a final answer | 100% | 0% |
| unparseable replies per task | 0.31 | 3.33 |
| tool calls per task (ok) | 6.1 (98%) | 2.0 (97%) |
| seconds per task | 47.4 | 27.0 (1.76x faster) |

Why: Jan is trained for native tool calling (the chat template's tool-call format). Asked for our text-JSON action
protocol it writes single `{"name", "args"}` calls or invents a tool's output (copying price data from its context)
instead of requesting it. A fair test would need native tool calling in agent_worker; not worth it while research
briefs feed no decision (stock-picking verdict). Speed note: research time is ~all GPU (tool calls replay from the
cache in ~0 s), so if research becomes a bottleneck again the free wins are OLLAMA_NUM_PARALLEL=4 (the 4 workers were
queueing on a 1-slot server), OLLAMA_FLASH_ATTENTION=1 and OLLAMA_KV_CACHE_TYPE=q8_0; vLLM (prefix caching of the
re-sent research history) is the next step after that. Post-hoc ternary quantization of a 4B model is not worth it.

## Stage B (2026-09-25): all stock signals combined, walk-forward, in the master portfolio

Stock picking continues (the user's call; failed tests are findings, not a stop). scripts/combine_scores.py: EPS
change, Bonsai P(BUY) with the research table, momentum, guidance -> logistic regression refit monthly on releases
whose 20-day outcome was already known; 2,672 S&P 500 releases scored Mar 2024 - Aug 2026 (results/events/
combined_eval.json). Combined score 20-day IC +0.033 [-0.032, +0.097], 60-day +0.071 [+0.007, +0.134]; Bonsai alone
+0.075 [+0.020, +0.129] at 20 days on the same releases (the regression's weights: Bonsai +0.08, EPS +0.04, momentum
+0.03, guidance ~0).

Master agent (calibrated quarter-Kelly, caps, crypto sleeve, SPY core), 2024-03-01 -> 2026-09-24:

| Portfolio | CAGR | Sharpe | Max DD |
|---|---|---|---|
| SPY + crypto sleeve + stock picks (combined score) | 20.93% | 1.20 | 21.0% |
| SPY + crypto sleeve only | 18.76% | 1.10 | 20.8% |
| SPY | 18.15% | 1.16 | 18.4% |

Stock picks added ~2 points a year over the crypto sleeve alone. Caveats: 2024 Bonsai answers are inside its training
data; 2.5 years; the 2025-26 part is a 1,180-release sample. Next: web research per release (below), then all
2025-26 releases, then a stock book in the forward test.

**Web research per release** (scripts/research_events.py): the qwen3:8b agent with the as-of internet tools researches
each release as of its SEC acceptance time, writes a source-checked brief, and the verified facts join Bonsai's fact
sheet. Test: 400 random 2025-26 releases, Bonsai with vs without the research (oos_research400_with/_without.json).
