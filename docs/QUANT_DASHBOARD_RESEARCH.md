# What other quant / AI-trading projects do, and what airp took from them (30 Sep 2026)

Ideas and formulas were re-implemented from their descriptions; no code was copied. Nothing here changes the frozen
live book, and no trial was run or registered.

## Projects read
| Project | What it is | Worth taking |
|---|---|---|
| zaydabash/ema-sharpe-dashboard | 5 rule strategies + backtest dashboard | rolling metrics, Monte Carlo bootstrap of final equity, cost/slippage inputs, trade blotter |
| Daniswara369/ai-quant-trading-dashboard (Quantryst) | Analyst / sentiment / risk-auditor agents + a manager | per-agent reliability % (rolling win rate) used as consensus weights, a "no-trade zone" floor, half-Kelly sizing, live agent reasoning stream |
| MaximeFARRE/Quantitative-Finance-Dashboard | single-asset + portfolio dashboard | correlation heatmap, risk contributions, diversification ratio, effective number of assets, turnover |
| initial-d/ml-quant-trading (validation dashboard) | factor backtest validation | cost stress test (0/7/15/30 bp), turnover and cost drag |
| TauricResearch/TradingAgents | analysts -> bull/bear debate -> trader -> risk -> PM | parallel analysts, deep-vs-quick model split, memory of past decisions with realised alpha, checkpoint resume |
| Kantamaniprakash/trading-agents-lab | TradingAgents with an honest evaluation harness | prompt anonymisation (mask ticker and dates, rebase prices to 100) to stop the model recalling history; disk cache of every LLM reply; finding: the agent desk lost to buy-and-hold |
| alex-jb/orallexa-ai-trading-agent | 8-source signal fusion + bull/bear/judge | per-source accuracy ledger -> dynamic weights; regime-aware agent subsetting (about half the LLM calls); decision card with debate transcript |
| virattt/ai-hedge-fund | many persona agents + risk/portfolio managers | hides tickers and calendar dates from agents in backtests (t-0, t-1 labels) |
| microsoft/RD-Agent (Q) | LLM loop that proposes factors/models and backtests them | IC / ICIR / rank-IC tracking per iteration |
| ranaroussi/quantstats | the standard tear sheet | Sortino, Calmar, VaR/CVaR, ulcer index, tail ratio, monthly heatmap, drawdown periods, rolling vol/beta, Monte Carlo |

## Taken and built (desktop app + `backend/scripts/desktop_export.py`)
- Tear sheet per book: Sortino, Calmar, VaR 95 / CVaR 95, ulcer index, tail ratio, skew, kurtosis, best/worst day and
  month, share of up days/months, longest time under water, beta, alpha, correlation, up/down capture, information
  ratio, probabilistic Sharpe ratio.
- Month-by-month heatmap, five worst drawdowns (start, trough, recovery, days), monthly-return distribution,
  rolling 6-month volatility and beta.
- Block-bootstrap cone for the next 12 months (21-day blocks, 2,000 paths, seeded): median, 5-95% band, chance of
  ending lower, chance of a 20% fall.
- Portfolio & risk page: allocation through time (targets x drawdown brake), allocation today, risk contributions,
  correlation matrix, diversification ratio, effective number of independent bets, trades and costs paid.
- Strategy lab detail: Sharpe at each tested cost level (cost stress), before-publication Sharpe, correlation with
  the core book, hit rate, worst month.
- AI team view: one card per live release with every agent's opinion (reader facts, summary agent, bull case, bear
  case, AI-theme agent, judge score and verdict, result when it matures).
- AI evidence: score deciles vs 5-day sector-relative result and hit rate, monthly rank IC, score-vs-result scatter,
  per-sector table, above/below-threshold comparison (3,160 past releases; descriptive, in-sample for the threshold).

## Already in airp or already tested (do not re-propose)
- Trend / moving-average / momentum rules: `trend_sleeve`, `b0_daily_trend_check`, `momentum_volmanaged` (fail).
- Volatility targeting: `vol_target_b0_20pct` (fail). Inverse-vol / risk parity: `skfolio_cvar_risk_parity` (fail).
- Pairs: `pairs_ggr`, `statarb_arm_a` (fail). Fractional Kelly sizing and position caps: already in `MasterConfig`.
- Bull/bear debate, summary reader, AI-theme lens, self-improving prompt versions: live as shadows.
- LLM reply cache and code-verified quotes: already there.

## Candidates for after 6 Oct (each needs its own pre-registration; nothing run yet)
1. **Reliability-weighted consensus (shadow).** Weight each opinion agent by its own forward hit rate and combine
   with the judge's score. Needs matured outcomes first (the shadows started 30 Sep), so the earliest honest test is a
   forward shadow, judged like the other shadows.
2. **Anonymised-prompt arm.** Re-score a past sample with ticker, company name and dates masked. If the judge's
   backtest edge shrinks, part of it was memory of history, not reading skill. This is a validity check on every Bonsai
   backtest; the forward test is immune.
3. **Decision memory.** Give the judge its own last decisions on the same company with the realised result
   (TradingAgents-style reflection). Forward shadow only.
4. **Regime-aware agent subsetting** is a speed idea, not an alpha idea: skip agents that add nothing in the current
   regime. Only worth it once item 1 shows which agents matter.

## Making the research agents faster (measured on this PC, 30 Sep)
- Bonsai labels: about 5.6 s per release = 1.8 s reading about 2,300 prompt tokens (1,200 tok/s) + 3.5 s writing about
  240 tokens (68 tok/s). About 475 prompt tokens per call already come from the prefix cache.
- **Only one request runs at a time.** Ollama 0.34.0 logs "model architecture does not currently support parallel
  requests" (architecture qwen35) and starts the model with one slot, so `OLLAMA_NUM_PARALLEL=3` and the client's
  `concurrency=3` are ignored. 4.9 of 12.2 GB VRAM is used. The fix (ollama PR 17144) is still an **open** pull
  request; the newest release (v0.35.0, 28 Sep 2026) does not contain it, so updating Ollama does not help.
- **Measured fix, no download (30 Sep, 24-36 real arm-A prompts against Ollama's cached replies):** the engine Ollama
  bundles (`/usr/local/lib/ollama/llama-server`, started directly with `--jinja`, `enable_thinking: false` and JSON
  output) gives:

  | Slots | Seconds per release | Speed | Replies identical to Ollama | Field agreement |
  |---|---|---|---|---|
  | 1 | 5.66 | 1.0x | 24/24 | 100% |
  | 3 | 3.44 | **1.63x** | 22/24 | 98.2% |
  | 6 | 4.03 | 1.39x | 30/36 | 97.2% |

  One slot reproduces Ollama exactly, so the request format is equivalent; the differences at 3 and 6 slots are
  floating-point effects of batching. Three slots is the sweet spot. No crash in about 110 requests.
  **Not switched yet:** the label client speaks Ollama's API, so it needs a small OpenAI-style client and a launcher,
  and a registered arm labelled this way differs from a one-slot arm in about 2% of fields (random, not directional).
  To be adopted with a dated note in PLAN_60_V2 when the next label stage starts.
- **The news crawl is the real bottleneck.** GDELT's DOC API limiter is stricter than its documented one request per
  5 s; at the current rate the 2,851-release warm-up needs about 400 more hours. Faster sources (GDELT on BigQuery,
  grouped OR queries) change the frozen B4b news query, so they need a new pre-registration and, for BigQuery, the
  user's OK because queries beyond the free tier cost money.
- `warm_gdelt.py` progress line under-reports coverage (it counts from the event rows, not the done records); the
  gate computes coverage separately. Fix when the job is not running.
