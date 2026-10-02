# AIRP Local 5070: can a small local AI pick stocks?

A research project that tests, honestly, whether AI models running on one home graphics card (an RTX 5070, 12 GB)
can pick stocks better than just buying the S&P 500. It uses **paper money only** (no real trades) and **free data
only** (SEC filings, Yahoo prices, Wikipedia, the Internet Archive). Nothing here is investment advice.

## The short answer so far

- **Mostly no.** Most AI stock-picking ideas tested here did no better than chance or than simply holding SPY.
- **One lead:** a "1-week" book that reads each company's earnings release and buys the ones the AI likes, held
  for a week. In a backtest it slightly beat the no-stock-picking version of the same portfolio, but only if
  trading is cheap, and it is **not proven**. It still has to pass a forward test on new data.
- Most of the money in every portfolio below comes from the **S&P 500 (SPY)** and a small **bitcoin/ether trend
  sleeve**, not from the stock picks.
- **The book going into the forward test (from 5 Oct 2026):** SPY + the crypto trend sleeve (at most 20%) +
  drawdown brakes. The AI picks run beside it as a shadow book with no money. 2018-2026 backtest without brakes:
  21.6% a year, worst drop 34%; the brakes cut the worst drop to 27% at the same risk-adjusted return. Plan and every
  test: [`docs/PLAN_60_V2.md`](docs/PLAN_60_V2.md).

## How it works

Every quarter each S&P 500 company files an **earnings press release** with the SEC. For each one:

1. **Read.** A small AI model reads the press release and pulls out revenue, earnings per share (EPS) and
   guidance. Code checks every number against the release's own text and against the company's SEC filings,
   and drops numbers it can't confirm (unit mix-ups, typos).
2. **Fact sheet.** Code writes a one-page fact sheet: the checked numbers, growth vs a year earlier, recent
   price moves vs the stock's sector.
3. **Web research (being tested).** A research agent, **Jan-v1-4B**, looks things up *as of the moment the release
   came out*: the release itself, the company's previous release (to see whether it met its own guidance),
   archived news, price history. Every tool refuses anything published after that moment, so a backtest can't
   peek at the future. **Bonsai-27B** then writes a short brief; code keeps only facts whose numbers really appear
   in the cited source.
4. **Decision.** Bonsai-27B, a 27-billion-parameter model compressed to 1 bit per weight (4 GB), reads the fact
   sheet and answers BUY or PASS for each holding period ("book"): 1 week, 1 month, 3 months, 6 months, 1 year.
   How sure it is comes from its token probabilities.
5. **Portfolio.** A master agent turns those scores into positions: the top fifth of a book's picks, a crypto
   trend sleeve (BTC/ETH, at most 20%), and the rest in SPY. Trades fill at the next day's open, with costs.
6. **Scoring.** Every test has its pass rule written down **before** its results exist (see
   [`docs/WEEK_PLAN.md`](docs/WEEK_PLAN.md)), and results that only show up afterwards are marked as leads, not
   findings.

Everything runs locally: Jan on vLLM (FP4 on the 5070's tensor cores), Bonsai on Ollama. Since 29 Sep 2026 the
paper book runs by itself on four systemd user timers; how, when and what it writes is in
[`docs/HOW_IT_RUNS.md`](docs/HOW_IT_RUNS.md), the one current description of the running system.

## How much money could it make?

Backtest, 2024-03-01 to 2026-09-24 (about 2.5 years), **$10,000 of paper money**, trades filled at the next open:

| Portfolio | Cost per trade | Ends with | A year | Sharpe |
|---|---|---|---|---|
| SPY only | - | $15,340 | 18.1% | 1.16 |
| SPY + crypto sleeve, no stock picks | 10 bps | $16,100 | 20.4% | 1.18 |
| **SPY + crypto sleeve + 1-week AI picks** | 10 bps | **$16,450** | 21.4% | 1.24 |
| same, if trading were free | 0 bps | $17,490 | 24.3% | 1.38 |
| same, with expensive trading | 25 bps | $14,980 | 17.1% | 1.02 |

What this means in plain terms:

- On $10,000 the AI picks added about **$350 over 2.5 years** at realistic costs compared with the same portfolio
  without them, and **lost** money once costs reached ~16 bps per trade. The 1-week book trades a lot.
- It beat 47 of 50 runs where its scores were shuffled at random (p = 0.06): suggestive, not proof.
- Part of the backtest (2024) is inside the AI's training data, and this was one 2.5-year path. A forward test on
  new releases is the real test.
- The longer books (3 months, 6 months, 1 year, 2 years) **failed** their tests: good in 2024, bad in 2025-26.

So the honest answer is: **so far, expect roughly what SPY + a small crypto sleeve makes; the stock picks are an
unproven extra of about a point a year that costs can erase.**

## What has been tested (and failed)

| Idea | Result |
|---|---|
| LLMs predicting next week's price move from prices | no better than always guessing "up" (7 runs) |
| Web research added to the fact sheet (v2) | made every book slightly worse |
| Analyst price targets from archived Yahoo pages | 0 of 12 pre-registered tests passed |
| 3-month / 1-year / 2-year books | failed (sign flips between years) |
| Speed tricks: speculative decoding, bigger batches, shorter brief format | measured; kept only what helped |
| Web research v3 (faster, aimed at the earnings surprise), 5 books | no book improved (1 week: IC +0.102 with vs +0.103 without) |
| 1-month AI picks as a satellite in the portfolio | no measurable gain (Sharpe +0.01, interval −0.08 to +0.13) |
| Value + quality (SEC fundamentals), momentum, trend, FX carry, stat-arb, long-short event book | all failed their pre-set rules |
| Volatility target; a 35% crypto cap | slightly better, but inside the noise: kept as leads, not adopted |
| "Analyze a sudden spike before responding" (Jan + Bonsai explain each 4σ move) | explaining works; trading on it lost money (−0.19% per spike), so it is information only |
| **Drawdown brakes** (cut risk to ⅔ at −10%, ½ at −20%) | **passed**: worst drop 33.8% → 27.2% at the same risk-adjusted return |

Details: [`docs/WEEK_PLAN.md`](docs/WEEK_PLAN.md) (this week's tests, rules and results),
[`docs/EXECUTIVE_SUMMARY.md`](docs/EXECUTIVE_SUMMARY.md), [`docs/PLAN_60_V2.md`](docs/PLAN_60_V2.md) (the current plan and
every test since 26 Sep), [`docs/STRATEGY_RESEARCH.md`](docs/STRATEGY_RESEARCH.md) (literature review).

## Running it

Needs Linux, an NVIDIA GPU with 12 GB, [Ollama](https://ollama.com) with `bonsai-27b`, and Python 3.12.

```bash
cd backend && python -m venv .venv && .venv/bin/pip install -e .
.venv/bin/python -m pytest -q                        # 380 tests
cd .. && scripts/research_v3_run.sh 24               # research + decisions on 24 releases
```

Main pieces:

| Path | What it is |
|---|---|
| `backend/scripts/build_features.py` | fact sheets, with the SEC cross-check |
| `backend/scripts/research_events.py` | Jan's as-of web research and Bonsai's source-checked briefs |
| `backend/scripts/decide_events.py` | Bonsai's BUY/PASS per book |
| `backend/scripts/combine_scores.py`, `master_portfolio.py` | combined score and the paper portfolio |
| `backend/app/tools/asof.py` | the "as of" internet tools that refuse the future |
| `scripts/*_run.sh` | the manual run scripts (one GPU job at a time) |

---

## Experiment history (technical)

The sections below are the earlier write-ups, oldest ideas first in places; the tables above are the current state.


> **This repo is `airp-local-5070`, a clone of `airp-local` tuned for a single
> RTX 5070 (12 GB).** It adds a leakage-proof walk-forward test of a local LLM
> forecasting agent on **real** prices: a bubblewrap process jail (no network,
> no data access), point-in-time data and memory, anonymized inputs, a test
> window after the model's measured training cutoff, a self-improving agent
> arm, and non-LLM baselines on the identical grid. It also fixes a cache
> bypass in the original sandbox. **Headline result: across seven frozen runs, up to
> 5,200 post-warm-up predictions each, nothing beat simply predicting "up"** —
> not qwen3 8B or 14B (plain, self-improving, with SEC fundamentals, or scored
> from token log-probabilities), not a deep-RL agent trained on all of them, not
> the Kronos candlestick foundation model, and not a classical anomaly
> composite. Every success criterion was fixed before its run, and all of them
> failed; see
> [`docs/WALKFORWARD_5070.md`](docs/WALKFORWARD_5070.md) for the full results,
> the research behind the design, and how to reproduce.

### Phase F: research-driven optimization (run 2026-09-22 — not passed)

Reading the LLM's probability from token log-probabilities fixed the measured tie problem (9 distinct
probabilities over 6,400 forecasts became 3,200) and ranked better than the verbalized answers
(+0.019 rank IC [+0.0001, +0.0371]) — but both sit at rank IC ≈ 0, and the raw log-prob probabilities are far too
confident to use as probabilities (Brier 0.45 vs 0.25). Kronos and the anomaly composite did no better. The
Lookahead Propensity probe found no memory to leak: for a real ticker and date the model answers "unknown", and
its date-only recall is 47% — chance. Measured on the way: a tuned Ollama configuration is 1.6× faster (off by
default). See [`docs/RESEARCH_OPTIMIZATION.md`](docs/RESEARCH_OPTIMIZATION.md), `docs/WALKFORWARD_5070.md` and
`docs/EXECUTION_PLAN.md` (Phases E and F). `scripts/phase_f_pipeline.sh` re-runs the whole GPU chain, one job at
a time, in the foreground.

### Paper-trading simulator (fake money, free data)

`cd backend && .venv/bin/python scripts/simulate.py v7_phase_f` trades every strategy's weekly picks with $100k of
paper money: fills at the next day's open with slippage, a 20% drawdown halt, rejected bad model outputs, random
tie-breaks, and beta/alpha against SPY. Best result: +62% vs SPY's +18%, but it came from holding 1.6x-beta stocks
in a rising year (alpha +25%, 95% CI −20% to +71%), and the same model came last in the 20-day run. See
[`docs/SIMULATOR.md`](docs/SIMULATOR.md).

### 16 years, and an LLM that researches the internet as of each date

`scripts/build_history.py` builds a point-in-time S&P 500 top-100 universe for every year from 2010 to 2026:
Wikipedia revisions for membership, free Yahoo prices. `scripts/longrun.py` paper-trades the no-LLM strategies
over 2011–2026. Momentum made 19.9% a year vs SPY's 14.0%, but its alpha after beta, +4.0% a year
(95% CI −4.3% to +12.1%), is not distinguishable from zero. The learned model did worse than the simple rules.

`app/tools/asof.py` gives the jailed LLM internet tools that return only what existed at the decision time:
Wikipedia revisions, SEC filings already accepted, and Internet Archive captures of news pages.
`scripts/llm_web_backtest.py` has it re-rank each month's 20 screened stocks, and `scripts/llm_web_report.py`
tests whether it beats the screen. Only 2025–2026 decisions count as evidence, because the model may remember
earlier years. Result on 400 clean decisions (Feb 2025 – Sep 2026): the LLM's ranking skill was −0.07 (95% CI −0.19 to +0.04),
no better than the screen it re-ranks, and its top 10 made +4.2% vs SPY's +30.8%. See [`docs/LONG_HISTORY.md`](docs/LONG_HISTORY.md).

### Live, web-informed research

```bash
cd backend && .venv/bin/python -m app.live.research NVDA JPM
```

The jailed agent researches current prices, news, articles and SEC filings
through guarded tools (about 8–20 s per ticker on a 5070), then returns
P(up) with reasoning, sources, and a full tool trace. Web tools are live-only:
backtests refuse them because today's web already contains the future. See
[`docs/LIVE_TOOLS.md`](docs/LIVE_TOOLS.md).

### Results dashboard (Python)

```bash
cd backend && .venv/bin/pip install -e ".[ui]"
.venv/bin/streamlit run app/dashboard/app.py      # opens http://localhost:8501
```

Browse every run: scores, week-clustered confidence intervals against
"always up", accuracy and long/short growth over time, calibration, the
self-improving agent's lessons and stacker decisions, every individual
prediction, a cross-run comparison, the memorization probe, and a form to
launch new runs on your local Ollama models with live progress.

### Paper portfolio viewer (live)

```bash
scripts/portfolio_ui.sh          # http://localhost:8502
scripts/portfolio_ui.sh --lan    # also from your phone on the same Wi-Fi
```

Read-only view of the forward test: each book's holdings, live equity, today's move, drawdown
and brake level (marked at Yahoo's latest prices every minute); orders waiting to fill; every fill;
the AI picks (open picks' return vs sector so far, closed picks' 5-day result, missed releases);
and run health (last runs, weekdays with no run, ledger hash check). It never trades or writes: the
books change only when `forward_allocator.py` / `forward_events.py` run (on their timers, see
`docs/HOW_IT_RUNS.md`, or by hand).

A local-first AI investment research platform: runs against **your own GPUs**
(no cloud LLM API keys required) and includes a **point-in-time sandbox** for
honestly backtesting predictions — the data connectors physically reject
anything timestamped after the date you're testing against, so a backtest
result here means what it says.

This is a sibling of [AIRP](../airp) (the cloud-oriented original), not a
fork that diverged by accident — see "How this differs from AIRP" below.

## What's actually working right now

- **Sandbox / Backtest harness** (`backend/app/sandbox/`): pick a ticker and
  a historical date, get a prediction generated using only data available as
  of that date, then see it graded against what actually happened. Every run
  self-checks that the lookahead guarantee held and reports that
  transparently. **Fully wired end-to-end**, including a TypeScript UI with
  persisted history across restarts — see `docs/SANDBOX.md`.
- **Context compression** (`backend/app/context/`): per-ticker compressed
  research material with token-budget-aware retrieval, so agents don't need
  an ever-growing prompt to know "what do we know about this ticker" —
  compression never severs the link back to evidence. See
  `docs/CONTEXT_COMPRESSION.md`.
- **Local LLM routing** (`backend/app/llm/`): configure two Ollama endpoints
  (e.g. your 5070 for reasoning, your 3060 for extraction) and the backend
  routes agent calls to the right one. See `docs/LOCAL_SETUP.md`.
- **Deterministic quant engine, evidence/claim model, debate orchestrator,
  knowledge graph schema** — carried over from AIRP unchanged, since none of
  it is LLM-provider-specific. See `docs/ARCHITECTURE.md`.
- **Web UI** (`frontend/`, Next.js + TypeScript): a working Sandbox page with
  single-run, multi-date-suite, and persisted history views; a live-research
  view is the next milestone (see `docs/ROADMAP.md`).

## Quickstart

```bash
# macOS/Linux
./scripts/setup.sh
# Windows (PowerShell)
.\scripts\setup.ps1
```

Then, in two terminals:
```bash
cd backend && source .venv/bin/activate && uvicorn app.main:app --reload   # .venv\Scripts\Activate.ps1 on Windows
cd frontend && npm run dev
```

Open `http://localhost:3000/sandbox` and run a backtest. With no LLM
endpoints configured, everything still works — the quant/sandbox mechanism
doesn't depend on an LLM at all; only the (not-yet-UI-wired) narrative
synthesis agents would fall back to a mock response. See
`docs/CROSS_PLATFORM.md` for OS-specific notes.

## Quickstart (with your two GPUs)

See `docs/LOCAL_SETUP.md` for the full walkthrough: installing Ollama on
both machines, exposing it to your LAN, picking models sized for a 5070 and
a 3060, and pointing `backend/.env` at both.

## Repository layout

```
airp-local/
├── scripts/                setup.sh (macOS/Linux) and setup.ps1 (Windows)
├── backend/
│   ├── app/
│   │   ├── llm/            LLMClient protocol, Ollama-compatible client, GPU router
│   │   ├── sandbox/        point-in-time clock, lookahead prevention, backtest runner
│   │   ├── context/        per-ticker compressed context store, token-budget retrieval
│   │   ├── store/          SQLite-backed backtest history (stdlib only, no compiled deps)
│   │   ├── quant/          deterministic valuation/risk/options library (unchanged from AIRP)
│   │   ├── evidence/       claim + provenance model (unchanged from AIRP)
│   │   ├── debate/         adversarial multi-agent orchestrator (unchanged from AIRP)
│   │   ├── agents/         specialist agents
│   │   ├── data_ingestion/ connectors — sandbox-aware mock connectors included
│   │   └── api/routes/     FastAPI routes, including /api/sandbox/*
│   └── tests/              pytest — 332 tests
├── frontend/               Next.js + TypeScript UI (Sandbox page is live)
└── docs/                   ARCHITECTURE, API_SPEC, AGENT_INTERFACES, ROADMAP,
                            LOCAL_SETUP (two-GPU walkthrough), SANDBOX,
                            CONTEXT_COMPRESSION, CROSS_PLATFORM
```

## How this differs from AIRP (the original cloud-oriented repo)

| | AIRP | AIRP Local |
|---|---|---|
| LLM provider | Anthropic API | Your own Ollama endpoints, routed by role across up to 2 GPUs |
| Persistence | Postgres + Neo4j required | SQLite (stdlib, zero compiled deps) by default; Postgres/Neo4j optional extras |
| Backtesting | Not built (flagged as the top ROADMAP gap) | Built: `app/sandbox/`, enforced at the connector level, self-checking, persisted history |
| Context management | Not built | `app/context/`: compressed, token-budgeted, evidence-preserving |
| UI | Not built | Next.js/TypeScript, Sandbox page functional today |
| Default install | Includes asyncpg/neo4j (platform-specific wheels) | Minimal — no compiled deps in the default install, `neo4j` is an opt-in extra |
| Quant engine, evidence model, debate orchestrator | Same code | Same code — these never depended on the LLM provider or persistence choice |

If you want the cloud/Postgres/Neo4j-backed version, that's the other repo.
This one is for running entirely on hardware you own, and for being able to
actually check whether the predictions are any good before trusting them.

## Verified state (as of last commit)

Everything below was re-run for this repo; claims inherited from upstream that weren't re-checked were removed.

- **CI is green on GitHub** (it had failed on every push until 2026-09-14, and again on 2026-09-16 when a new pandas-stubs release broke one type check; fixed the same day): `ruff`, strict `mypy` on 60 modules
  plus `mypy` on all 85, the full pytest suite, and an 85% coverage floor (currently 95%) on the
  safety-critical modules. Hosted runners have no bubblewrap, so the jailed variants skip there and the same
  checks run unjailed; locally (Ubuntu 26.04, bubblewrap installed) the jailed variants run too.
- **Reproducibility:** `python scripts/reproduce.py` re-runs every frozen backtest from the committed LLM cache
  and compares every score and individual prediction with the published files: all identical, no GPU needed.
- **Isolation:** every walk-forward run probes the jail live at start and end, and refuses to run if the agent
  can read the data or reach the network. Hostile-worker tests cover memory, CPU, hangs, floods, and
  oversized messages.
- **Web tools:** the network guard connects only to the exact public address it checked (DNS rebinding
  closed), with size and total-time limits; live research fits the model's context (measured, with
  overflow detection).
- **Results:** v5, v6 and v7 finished 2026-09-22 and were scored against criteria fixed before they ran — all
  NOT PASSED; each replays identically from its committed cache with no GPU (`scripts/reproduce.py`).
- **Forward test (2026-09-27):** `scripts/forward_allocator.py` (SPY + crypto books, with and without brakes) and
  `scripts/forward_events.py` (new S&P 500 releases → Bonsai's 1-week score, logged before the open in a
  hash-chained ledger) are ready; `scripts/weekly_review.py` summarizes both. Since 29 Sep they run on timers
  (`docs/HOW_IT_RUNS.md`): the event runner twice each weekday (08:45 and 18:30 ET). A dry run over 8-25 Sep 2026 found and fixed two bugs; the second dry
  run decided 6 of 6 releases on time. Starts Mon 5 Oct.
- **Forward test (v1, older):** hash-chained ledger verified by the dashboard and by tests; first decision logged on time.
  **Paused 2026-09-16:** the hourly timer was uninstalled at the owner's request, so weeks from 2026-09-21 are not
  logged unless `python -m app.forward.run` is run by hand before the Monday open (missed weeks can't be backfilled).
- **Failure handling (2026-09-16):** a power loss that corrupted the LLM cache and a GPU crash (Xid 79) that made
  Ollama fall back to CPU are both handled now: damaged cache lines are skipped, CPU answers are refused, and GPU
  jobs take one shared lock.
- Frontend (`frontend/`) is the upstream Next.js app, built in CI; it has not been extended for the walk-forward
  work (the Streamlit dashboard covers that).

## License

MIT — see `LICENSE`. Produces research and trade *proposals* only; nothing
in this repository executes trades.
