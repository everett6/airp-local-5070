# 16-year history and the internet-connected LLM

Two additions, both on free data and fake money only:

1. **A 16-year backtest (2011 to Sep 2026)** of the no-LLM strategies. It uses a stock universe that changes every
   year, as the index did.
2. **Internet research for the LLM in backtests.** Every lookup is limited to what had been published by the
   decision date.

## Data (free)

`backend/scripts/build_history.py` builds the stock list and prices:

- **Membership:** on each Jan 1 from 2010 to 2026, the S&P 500 members come from the last Wikipedia revision of
  *List of S&P 500 companies* before that date. Each revision is cited in `data/hist/universe_2010_2026_top100.meta.json`.
- **Universe:** the top 100 members by average daily dollar volume over the prior 252 trading days, with one
  share class per company. That list is held for the calendar year. In total, 211 stocks were ever in it.
- **Prices:** daily OHLCV from Yahoo (`auto_adjust=True`), 2009 to 2026, in
  `data/hist/ohlcv_2010_2026_top100.parquet`. Its sha256 is in the meta file.

**Known bias: survivors only.** Yahoo has no history for most companies that were later delisted, taken private
or bought out, so they can't be ranked or held. The share of members missing falls over time: 186 of about 500
in 2010, 59 in 2020 and 11 in 2026. Examples include Celgene, Kraft, Yahoo, EMC, Hess and Electronic Arts. The
meta file lists them for every year.

This bias raises every long strategy's return. The comparisons that hold up are therefore *relative* ones: a
strategy vs an equal weight of the same universe, or the LLM vs the screen it re-ranks. Fixing it for free would
need a free source of delisted prices, and none was found.

## No-LLM strategies over 16 years

Command: `python scripts/longrun.py` (about 20 s on the CPU). Setup:

- weekly rebalance into the top 10 at the next open, 5 bp cost per side, $100k of fake money;
- 2010 is used only as a warm-up;
- the automatic stop after a 20% loss is off, because the market itself fell 34% in 2020 and a permanent stop
  would end every run there. The worst drop is reported instead.

| strategy | CAGR | max drop | Sharpe | beta | alpha/yr after beta [95% CI] |
|---|---|---|---|---|---|
| SPY buy & hold | 14.0% | 33.7% | 0.86 | 1 | — |
| Equal weight, same universe | 14.2% | 33.5% | 0.84 | — | — |
| momentum (12-1) | **19.9%** | 33.8% | 0.81 | 1.22 | +4.0% [−4.3, +12.1] |
| anomaly_rank | 13.1% | 33.0% | 0.82 | 0.88 | +1.0% [−2.9, +5.0] |
| low_vol | 11.9% | 37.7% | 0.82 | 0.79 | +0.9% [−2.8, +4.6] |
| feat_logit (learned) | 11.6% | 42.1% | 0.58 | 1.10 | −2.1% [−9.3, +4.9] |
| reversal (1 month) | 11.1% | 46.0% | 0.52 | 1.30 | −4.6% [−12.3, +3.6] |
| high_52w | 8.3% | 33.6% | 0.56 | 0.75 | −1.6% [−6.9, +3.5] |

How to read it:

- **Momentum** made the most: 19.9% a year vs SPY's 14.0%. Part of that is a higher beta (1.22). The rest,
  +4.0% a year, has a 95% CI of [−4.3%, +12.1%], so it isn't distinguishable from zero even over 15.7 years.
  It also lost to SPY in 5 of the 16 calendar years (2011, 2016, 2021, 2023, 2025); see
  `results/longrun_no_llm.json`.
- **The learned model** (feat_logit) did worse than simple rules. It had beaten them over the single year in
  `docs/SIMULATOR.md`, which is why one year of results means nothing.
- **Deflated Sharpe here only asks whether Sharpe is above zero.** Any long stock portfolio passes that over 16
  years, so the 0.9+ values say nothing about skill. The alpha CI is the test that matters.

## The LLM with internet research

### Tools

`backend/app/tools/asof.py` adds tools that look things up on the internet but return only what existed at the
decision time (4 pm New York on the decision day):

| tool | source (free) | how the date is enforced |
|---|---|---|
| `price_history_as_of` | local Yahoo file | rows up to the decision day only |
| `sec_filings_as_of` | SEC EDGAR submissions | acceptance time + 5 h (Eastern to UTC, never early) ≤ decision time |
| `filing_documents`, `read_filing` | EDGAR archives | acceptance time re-read from the filing's own header, every call |
| `wiki_as_of` | Wikipedia revisions | last revision before the decision time; timestamp re-checked |
| `wiki_search` | Wikipedia search | titles only, and only articles that already existed |
| `news_as_of` | Internet Archive captures of MarketWatch, Reuters, CNBC, Yahoo and Nasdaq quote pages | capture time ≤ decision time; a redirect to a later capture is refused |
| `archived_page` | Internet Archive | same as `news_as_of` |

Additional safeguards:

- **Plain web tools are refused.** In this mode, news search, web search and live pages are refused with an
  error; tests check this.
- **The model is jailed.** It runs with no network, as before, and only the gateway touches the internet.
- **Results are cached.** Every tool result goes into `results/webcache/`, keyed by tool, arguments and decision
  time, so a re-run replays exactly without contacting the web. Failures are not cached.
- **Rate limits are respected.** The Internet Archive blocks clients that send more than about 15 requests a
  minute, so it is paced at one request every 4 s. SEC is paced at its published limit.
- **Probabilities come from token log-probabilities.** The verbal answer clusters on 0.55, so after the research
  the model answers UP or DOWN once more, and P(UP) is read from the token probabilities, as in v7.

### What the tools cannot stop

What the tools cannot stop is the model's own memory. qwen3:8b was trained on web text through about 2024 and is
told the ticker and the date. For a 2015 decision it may simply *remember* what happened next. So:

- **2025-01 onward is the clean window.** These dates come after the model's training data; they are the only
  evidence.
- **2011–2024 decisions** are labelled `memory_risk`. They are reported separately and are not evidence of skill.

### Design

- **Screen:** each month, pick the 20 stocks of that year's universe with the best anomaly_rank score. No model or
  web is involved.
- **Research:** the LLM researches each of the 20 with the as-of tools, then gives P(the stock rises over 20
  trading days).
- **Test:** does the LLM's top 10 beat the screen's own top 10 of the same 20 stocks?

Commands:

- the clean window: `python scripts/llm_web_backtest.py`
- older years, sampled: `python scripts/llm_web_backtest.py --from 2011-01-01 --to 2024-12-31 --every 3`
- the report: `python scripts/llm_web_report.py`

The report gives:

- rank IC by month with a bootstrap CI, for the LLM and for the screen;
- the LLM's gain over the screen;
- paper trading vs SPY;
- how often each tool actually found something.

### Scoring (v2)

The first version (v1) asked "UP or DOWN?" right after the model's own written conclusion. The model repeated
that conclusion with near-certainty, and the answers were rounded: 43% of scores were at least 0.9999, so most
top-10 picks were decided by random tie-breaks.

v2 asks the question on the evidence alone: better or worse than the average S&P 500 stock? It keeps the
unrounded log-odds (`updown_lo` mode). The 400 clean decisions produced 400 distinct scores, with 0% ties.

### Speed

The average decision went from 49 s to 5.5 s:

- **Research 4 stocks at once.** 85% of a decision's time was network wait, so the waits now overlap. The GPU
  still answers one prompt at a time, so the answers are unchanged.
- **Fetch each news page's list of archived copies once and cache it.** Previously it was fetched once per page
  per month, and the Internet Archive allows about 15 requests a minute.
- **Replay from saved results.** A re-run replays research from the saved tool and LLM results in about 1 s
  per decision.

### Results: clean window, Feb 2025 – Sep 2026

400 decisions: 20 months × 20 screened stocks. Full numbers: `results/llm_web_v2_report_clean.json`.

| | result |
|---|---|
| LLM ranking skill (monthly rank IC vs realised 20-day return) | **−0.070** [95% CI −0.186, +0.041] |
| Screen's own ranking skill on the same 20 stocks | −0.026 [−0.143, +0.090] |
| LLM minus screen | −0.044 [−0.236, +0.152] |
| Paper trading, LLM top 10 | +4.2% (Sharpe 0.24, beta 0.72) |
| Paper trading, screen top 10 | +7.5% (Sharpe 0.34, beta 0.79) |
| SPY buy & hold | +30.8% |
| LLM vs screen, per year | −2.2% [−14.7, +10.6] |

Research coverage:

- 86% of decisions found archived news and 93% found SEC filings; 7% found neither.
- Tools succeeded 93–100% of the time.
- The model listed filing documents 622 times but never opened one with `read_filing`. With 2 research rounds,
  listing the documents used up the last round, so exhibits such as earnings releases were never actually read.

**Verdict: no evidence the internet research helps.** The LLM ranked the 20 candidates slightly *worse* than
chance and worse than the screen, though neither difference is distinguishable from zero over 19 scored months.
Both portfolios trailed SPY by a wide margin. The screen favours low-volatility, beaten-down stocks (beta
about 0.75), which lagged in a strong market.
