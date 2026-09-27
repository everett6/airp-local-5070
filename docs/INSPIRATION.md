# Outside projects reviewed (27 Sep 2026) and what was taken

Each item says what was built here, or why not. Anything that would change a book still needs a pre-registered test
(docs/PLAN_60_V2.md), and every such test is added to the trials registry.

| Project | What it is | Taken | Not taken, and why |
|---|---|---|---|
| [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) | Agent trading workspace with broker connectors | Mandate + fail-closed pre-trade gate + kill switch (`app/portfolio/guard.py`); price-caliber check (`bars.compare` ratio drift) | Live broker order placement: paper money only |
| [fidetolabs/qanat](https://github.com/fidetolabs/qanat) | DAG-of-tables backtest engine with an agent console | Console look for the viewer: stat strip, segment navigation, equity + underwater, run log | Its table-level as-of filtering: our as-of tools and leak audits already do this |
| [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) (closest match to "antilus traders"; no repo by that name was found) | Analyst team → bull/bear debate → trader → risk manager | Nothing new: we already keep a decision log and source-checked evidence | The debate layer: more LLM calls per decision, no out-of-sample evidence it adds IC; our own research arms added none |
| [shiyu-coder/Kronos](https://github.com/shiyu-coder/Kronos) | Candlestick foundation model | Already tested here (docs/SIMULATOR.md): +12.0% vs benchmark, 95% CI [−28.6, +55.8] | Another trial: no new reason to expect a pass |
| [skfolio](https://github.com/skfolio/skfolio) | Portfolio optimization on scikit-learn (HRP, risk budgeting, CVaR, CPCV, stress tests) | Candidate: risk-budgeted crypto sizing and synthetic stress tests of the frozen book | Needs a pip install (user OK) and a pre-registered test before it touches a book |
| [polakowo/vectorbt](https://github.com/polakowo/vectorbt) | Vectorized backtests, huge parameter sweeps | Candidate: faster robustness grids (like the 30-pair crypto grid) | Mass sweeps without trial counting are how overfitting happens; any sweep must be registered as one trial with its full grid reported. Commons Clause license |
| [microsoft/qlib](https://github.com/microsoft/qlib) | Quant platform: point-in-time data, Alpha158, model zoo | Idea: Alpha158-style price features as a code-only baseline for the event book | Its US data comes from the same Yahoo collector we use; benchmarks are on China's CSI300 |
| [microsoft/RD-Agent](https://github.com/microsoft/RD-Agent) | LLM loop that proposes factors, codes them, backtests, repeats | The shape matches "LLM proposes, code scores" | An automated factor search is a trial generator: every candidate is a trial, so the Deflated Sharpe bar rises with each loop. Only with a fixed budget, a held-out period never shown to the loop, and every candidate registered |

## Data sources (`app/data_ingestion/bars.py`, `scripts/price_crosscheck.py`)

| Source | Status | Key |
|---|---|---|
| Binance | Working, via the public mirror `data-api.binance.vision` (api.binance.com answers HTTP 451 to US users). Fallback for BTC/ETH in `forward_allocator.py` | none |
| Alpaca (IEX feed) | Code ready; the free Basic plan gives historical bars and the IEX real-time feed | user adds `ALPACA_API_KEY_ID` / `ALPACA_API_SECRET_KEY` to backend/.env |
| Polygon.io (Massive since 2026) | Code ready; free Stocks Basic: end of day, 5 calls a minute (paced) | user adds `POLYGON_API_KEY` |
| IEX Cloud | Shut down 31 Aug 2024; IEX prices come through Alpaca's IEX feed | — |

First cross-check (120 days): Yahoo vs Binance closes, median gap 0.07%, worst day 0.24%, return correlation 0.9997
(BTC) and 0.9998 (ETH). No flags.
