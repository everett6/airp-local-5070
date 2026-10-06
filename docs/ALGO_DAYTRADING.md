# Algorithmic day trading: executive summary (6 Oct 2026)

**Request:** a Renaissance-style day-trading book for the autopilot paper account. It uses price data only, no AI models, is as fast as possible, and uses high leverage.

**Bottom line:** the engine is built, fast and wired into the autopilot. Its strategy **failed** its one honest backtest, so it runs in **shadow**: it computes every signal and logs simulated trades, but sends no orders. To trade it live with real paper orders, create `backend/results/forward/algo/LIVE`. That decision is the user's.

## What large quant funds use (research)
- **Renaissance / Medallion** (public accounts):
  - It combines many tiny, weak, short-horizon price signals with statistical models.
  - It trades them thousands of times, so a small average edge adds up.
  - It is strict about costs and risk, and runs very high leverage on market-neutral books.
  - Its capacity is small (never above about $10 bn), because short-horizon edges are thin.
- **Intraday lead-lag:** the most liquid assets (SPY, QQQ) move first, and other ETFs follow seconds to minutes later.
- **Short-term reversal and residual mean reversion:** a stock that moved away from its market beta tends to come back.
- **Order-flow and volume pressure, and VWAP distance:** used as execution and short-term signals.
- **Ensembles:** funds combine all of the above in one regression, rather than trading any single rule.

## What was implemented

| Piece | What it does |
|---|---|
| **E1 signal ensemble** ([app/sandbox/minute_ensemble.py](../backend/app/sandbox/minute_ensemble.py)) | Nine weak price signals on 1-minute bars of 14 liquid ETFs (SPY IWM DIA XLF XLE XLV XLI XLY XLP XLU XLB XLC XLRE SMH): own 1/5/30-minute returns, leader (SPY or QQQ) 1/5-minute returns (lead-lag), 5-minute beta residual (stat-arb), bar close location, volume pressure and VWAP distance. All are scaled by each ETF's own volatility. |
| **Model** | One pooled ridge regression, predicting the next 5 minutes, refit monthly on past data only (walk-forward). |
| **Trading rule** | Every 5 minutes from 09:35 to 15:50, hold an ETF long or short only if the predicted move beats its round-trip cost. Flat by 15:55, so nothing is held overnight. |
| **Leverage** | 3.0x equity for the algo book, capped at 3.9x for the whole account (under Alpaca's 4x day-trading power). A 5% daily loss stop sets the algo book flat for the rest of the day. |
| **Fast engine** ([scripts/algo_engine.py](../backend/scripts/algo_engine.py)) | Event-driven:<br>• Bars are pushed by websocket at each minute's close.<br>• Session statistics are precomputed at the open.<br>• Features use only yesterday's and today's bars.<br>• Orders go out concurrently over kept-alive HTTPS connections, with reductions first. |
| **Speed** | On a replay of 5 Oct, a signal for all 14 ETFs took a median of **139 ms** after the bar (max 169 ms). The Alpaca paper order round trip from home is typically 20–80 ms. |
| **Autopilot wiring** | The autopilot starts and stops the engine with its Run button. The AI controller no longer touches the 14 ETFs, so the two books don't fight. The account risk gross cap was raised from 2.6 to 3.9. |

## Test result (pre-registered before any code, run once)

E1 used 2023-01-03 to 2026-09-25 out-of-sample, with costs of 0.5 bp per side for SPY and 1.5 bp for the others. Result: **FAIL**.

| Measure | Result | Needed |
|---|---|---|
| Net Sharpe | 0.38 | ≥ 1.0 |
| 95% confidence range | −0.98 to 0.90 | above 0 |
| Years | 2023 −0.07%; 2024 0.0%; 2025 +3.8%; 2026 +0.04% | all positive |

**Why it failed:** the combined signals predict 5-minute moves of only about 0.1–0.2 bp. That is less than the 1–3 bp it costs to trade. The model wanted a position in less than 0.01% of decisions. The edges Renaissance-type firms take at this horizon are captured in microseconds by co-located firms with direct exchange feeds. Free IEX bars and a home connection can't compete there, so more leverage would only multiply a near-zero edge.

## Honest limits
- Alpaca paper fills are simulated, and the free live feed is IEX, about 2–3% of volume. The history used for the test is the full SIP feed.
- **"A couple of ms":** the latency from home to Alpaca is tens of milliseconds, not a couple. That doesn't matter at a 5-minute horizon, and true high-frequency trading is not reachable from here.
- Earlier day-trading tests that also failed: intraday momentum, ORB, VWAP noise, end-of-day and open reversal, box/Darvas, pairs, and the AI earnings day trade. E1 is the 14th. See `backend/results/trials_registry.jsonl`.

## Files
- **Code:** `backend/app/sandbox/minute_ensemble.py`, `backend/scripts/e1_ensemble.py`, `backend/scripts/algo_engine.py`
- **Tests:** `backend/tests/test_minute_ensemble.py`, `backend/tests/test_algo_engine.py`
- **Results:**
  - `backend/results/e1_ensemble.json`
  - Live model: `backend/results/forward/algo/model.json`
  - Engine status and log: `backend/results/forward/algo/status.json` and `events.jsonl`
- **Rules:** docs/PLAN_60_V2.md, section "E1".
