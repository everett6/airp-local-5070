---
name: airp-free-data
description: Fetch market or filing data for airp-local-5070 from free sources (Alpaca, SEC EDGAR, Yahoo, FRED, Deribit). Use when writing a downloader or anything that calls an external data API.
---

# Free data sources

- **Free only.** No paid APIs or subscriptions. Keys come from `backend/.env` via `app/data_ingestion/bars.py::key(...)`
  and `app/data_ingestion/edgar.py::sec_user_agent()`; never print, log or hard-code them.
- Alpaca SIP bars: `bars.ALPACA`, `feed=sip`, `adjustment=split|all`; class shares use a dot (`BRK.B`); page with
  `next_page_token`; retry 429 with backoff; custom timeframes are 1-59 Min / Hour / Day only. See
  `scripts/intraday_stocks.py`, `scripts/premarket_stocks.py`, `scripts/daily_alpaca.py`.
- SEC EDGAR: stay under ~8 requests/s with the user agent (see `app/data_ingestion/edgar.py`).
- Yahoo via `yfinance` (auto_adjust); FRED CSV (`fredgraph.csv?id=...`, e.g. DTB3 cached at `data/fred_dtb3.csv`);
  Deribit public API for crypto funding (`scripts/funding_data.py`). Binance blocks US users.
- Make long downloads resumable (one part file per month, skip existing parts) and write under `backend/data/`
  (large files are gitignored; never commit them).
- A downloader must not look at results: fetch data only; analysis happens in the pre-registered test.
