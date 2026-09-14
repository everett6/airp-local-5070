# Live, web-informed research

The forecasting agent can use real-time tools (news, articles, prices, SEC
filings, a Python sandbox) to reach a decision **now**. The agent itself still
has no network: it runs in the same bubblewrap jail as the backtests and asks
the orchestrator to run tools for it.

```bash
cd backend
python -m app.live.research NVDA JPM            # P(up in 5 trading days), reasons, sources, tool trace
python -m app.live.research AAPL --rounds 2 --model qwen3:14b
streamlit run app/dashboard/app.py              # "Live research" tab: same thing, streaming in the browser
```

Decisions are saved write-once to `backend/results/live/<date>/<ticker>-<time>.json`
(gitignored). Each file holds the answer, the agent's plan for each round,
every tool call with arguments, timing, and a result preview, and provenance.

## Why this is live-only

A backtest asks "what would the agent have decided on 2025-08-01?". Today's
web already knows what happened after that date: articles get updated, search
ranking reflects later events, and prices are revised. There is no honest way
to give a past-dated agent web access. So:

- `ToolGateway(mode="backtest")` refuses every web tool and offers only
  offline tools (`python`). The published walk-forward runs are unaffected.
- The accuracy of web-informed decisions can only be measured **forward in
  time**. That is Phase B3's forward test, which logs decisions before their
  outcomes exist.

## Tools

| tool | source | typical latency (measured) |
|---|---|---|
| `price_history` | Yahoo Finance chart API | 0.1–0.25 s |
| `stock_news` | Yahoo Finance headline RSS | 0.1–0.5 s |
| `news_search` | Google News RSS + Bing News RSS, merged | 0.3–0.7 s |
| `fetch_page` | the article itself, reduced to main text; robots.txt respected | 0.3–1 s |
| `sec_filings` | SEC EDGAR submissions (needs `SEC_USER_AGENT`) | 0.4–0.6 s |
| `web_search` | Brave Search API, only if you set `BRAVE_SEARCH_API_KEY` | — |
| `python` | agent-written stdlib Python in a separate no-network jail | ~30 ms |

Repeat requests are served from a 5-minute cache in about 2 ms. A batch of six
different tools runs in parallel in about 0.6 s. End to end, one ticker takes
about 8–20 s on qwen3:8b (2–3 tool rounds). Several tickers run concurrently,
and the model stays 100% on the GPU at an 8k context.

Settings live in `backend/.env` (gitignored; see `backend/.env.example`).

## Safety model

Web content and LLM-chosen URLs are untrusted.

- **No network in the agent.** Tools run in the orchestrator, and only the
  tools the gateway lists for the current mode are callable.
- **Network guard** (`app/tools/netguard.py`):
  - http(s) only, on standard ports, with no credentials in URLs.
  - The host must resolve only to public IPs. Loopback (Ollama), your LAN,
    link-local and cloud metadata addresses are all blocked, and every
    redirect hop is re-checked.
  - Response size is capped at 2 MB and each request times out after 6 s.
  - Requests are rate-limited per host (SEC's 10 requests/second).
  - Residual risk: DNS rebinding between the check and the connection.
- **Validation.** Every call's arguments are checked against the tool's
  parameter spec. Batches are capped (6 per round from the agent, 8 at the
  jail boundary). Result text is capped, and a failing tool returns an error
  message to the agent instead of crashing the run.
- **Prompt injection.** The system prompt tells the agent that tool output is
  untrusted evidence, never instructions. The tools are read-only, so the
  worst a malicious page can do is cause more bounded, read-only tool calls.
- **Code execution.** `python` runs in its own bubblewrap jail with no network
  and no files, 256 MB memory, and a 10 s CPU and wall-clock limit.
- Tests: `tests/test_tools.py` covers blocked addresses, redirects, size
  limits, caching, SEC pacing, robots.txt, the Python sandbox's isolation,
  and the full research loop. All tests run offline.

## Known limitations

- Some publishers block automated readers (HTTP 403). The agent sees the
  error and moves on to other sources.
- Google News links are redirect tokens, so the agent can see those headlines
  but can't open the articles. Bing and Yahoo links are real article URLs.
- qwen3:8b tends to anchor at p_up ≈ 0.55. Whether web information improves
  on price-only forecasts is exactly what the forward test measures; don't
  assume it does.
- Yahoo and Google/Bing feeds are unofficial public endpoints meant for feed
  readers. Use them for personal research, at the pace this code enforces.
