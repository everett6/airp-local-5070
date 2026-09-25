# Paper-trading simulator

Turns a model's stock predictions into trades with **fake money** and measures the result against simply holding
SPY. No real orders are ever placed. Data is free: Yahoo daily OHLCV bars (`backend/scripts/fetch_ohlcv.py`).

```bash
cd backend
.venv/bin/python scripts/simulate.py v7_phase_f               # every strategy of a finished run
.venv/bin/python scripts/simulate.py v7_phase_f --top-k 20 --cash 50000 --slippage-bps 10
```

Output: a ranked table, and `results/sim_<tag>.json` with the metrics and daily equity curves.

## How a strategy trades

- **Each signal date** (weekly in v7), the model scores all 100 stocks. The simulator buys the `top_k` highest-scored,
  equal weight, capped at 10% each, keeping 2% in cash.
- **Trades fill at the next trading day's open**, never at the close the model saw, plus 5 bp slippage per side,
  in whole shares. Sells go first; buys are scaled down if cash runs short.
- **Positions are marked to market** at every close.

## Safeguards (anti-hallucination and anti-self-deception)

| Check | What it prevents |
|---|---|
| Model outputs are validated: a probability in [0, 1], a known stock, a trading day; everything else is rejected and counted | acting on a garbled or invented model answer |
| Execution strictly after the signal day (asserted in code) | trading on information the model couldn't have had |
| Books reconciled every day: cash never negative, only whole long positions | accounting bugs that inflate returns |
| **Drawdown halt**: 20% below the peak, sell everything and stop | letting a broken model ride losses to zero |
| **Random tie-breaks, 25 runs per strategy**, with the share of picks decided by ties reported | "top 10" silently meaning "first 10 alphabetically" when a model gives many stocks the same score |
| **Beta and alpha vs SPY** with a bootstrap CI | mistaking a bet on riskier stocks in a rising market for skill |
| **Deflated Sharpe** across all strategies simulated | the best of 16 strategies looking good by luck |

Two of these caught real problems on the first run:
- The earnings-surprise rule showed **+51%**. But 100% of its picks were decided by ties (it only outputs
  0.49/0.50/0.51), and the "top 10" was alphabetical. With random tie-breaks its median is **+24%**.
- The best strategy, `feat_logit`, showed **+62% vs SPY's +18%**. But its beta is 1.56: it held stocks that move
  about 1.6× the market in a rising year. Its alpha after that is +25% with a 95% CI of **[−20%, +71%]**, and it
  was the **worst** strategy in the 20-day run (+5%).

## Results (2026-09-24)

**`v7_phase_f`**: $100,000, top 10 of 100 stocks, weekly, 2025-08-28 to 2026-09-10.

| strategy | return | range over 25 tie-breaks | max drawdown | beta | alpha/yr [95% CI] |
|---|---|---|---|---|---|
| SPY buy & hold | 18.1% | — | 8.9% | 1.00 | — |
| equal weight, all 100 | 21.5% | — | 6.3% | — | — |
| logistic on price features | 62.1% | — | 15.4% | 1.56 | +25.4% [−20.4, +70.6] |
| logistic + fundamentals | 47.9% | — | 22.9% | 1.59 | +15.9% [−31.5, +61.3] |
| LLM + self-improvement | 30.1% | +14 … +39% | 18.9% | 1.10 | +9.6% [−24.4, +43.3] |
| earnings-surprise rule | 24.1% | +4 … +41% | 14.1% | 0.77 | +9.2% [−15.8, +32.4] |
| LLM, prices only | 23.0% | +5 … +38% | 14.4% | 1.10 | +4.2% [−27.3, +37.5] |
| Kronos | 22.5% | — | 17.2% | 0.59 | +12.0% [−28.6, +55.8] |
| deep RL | 20.0% | +5 … +30% | 18.6% | 1.02 | +2.3% [−21.2, +25.9] |
| LLM + fundamentals (log-prob) | 17.4% | +5 … +31% | 11.7% | 1.10 | −1.2% [−26.5, +24.8] |
| LLM + fundamentals | 9.5% | −2 … +20% | 16.1% | 1.10 | −7.7% [−37.7, +21.5] |

**`v6_fund_rank20d`** (20-day holds): the best is logistic + fundamentals, +60% vs SPY's +15%, but its beta is 1.70
and its alpha is +27.8% [−16.2, +70.3]. The price-only logistic that led v7 comes last, at +5%.

**Reading:** every alpha confidence interval includes zero, and no Deflated Sharpe reaches 0.95 (best 0.86). One
year of a rising market with 10-stock portfolios is too noisy to tell skill from luck, and the strategies that
"won" did it mostly by holding high-beta stocks. This agrees with the walk-forward scoring in `WALKFORWARD_5070.md`.

## Limits

- One year of history. The 100-stock universe was fixed point-in-time on 2025-06-02, so it has no survivorship
  bias inside this window, but a single year is a single market regime.
- Costs are a flat 5 bp per side. Price impact at larger sizes isn't modeled.
- Dividends are in the adjusted prices. There's no borrow or short selling: strategies are long only.
