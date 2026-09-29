"""Daily bars from free sources besides Yahoo, for cross-checks and as a fallback. Paper research only.

  binance   BTC/ETH (…USDT pairs) from Binance's public market-data mirror, data-api.binance.vision. No key, no account.
            (api.binance.com refuses US connections with HTTP 451; the mirror serves the same klines.)
  alpaca    US stocks/ETFs from Alpaca's free Basic plan, IEX feed (IEX is ~2.5% of volume, so volume is not the
            consolidated tape; prices track it closely). Needs a free Alpaca paper account's keys, which only the user
            creates and puts in backend/.env as ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY.
  polygon   Polygon.io (renamed Massive in 2026), free Stocks Basic plan: end-of-day bars, 5 calls a minute (paced
            here). Needs the user's free key in backend/.env as POLYGON_API_KEY (or MASSIVE_API_KEY).
  iex       IEX Cloud shut down on 31 Aug 2024; IEX prices are reached through Alpaca's IEX feed above.
Every function returns a frame indexed by date (naive, UTC day) with Open, High, Low, Close, Volume. The parsers are
separate from the HTTP calls so they can be tested without the network.
"""
from __future__ import annotations

import os
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

BACKEND = Path(__file__).resolve().parents[2]
COLS = ["Open", "High", "Low", "Close", "Volume"]
BINANCE = "https://data-api.binance.vision/api/v3/klines"
ALPACA = "https://data.alpaca.markets/v2/stocks/bars"
POLYGON = os.environ.get("POLYGON_BASE", "https://api.polygon.io")
BINANCE_PAIRS = {"BTC-USD": "BTCUSDT", "ETH-USD": "ETHUSDT"}
_last_polygon = [0.0]


class NoKeyError(RuntimeError):
    """A source that needs the user's own free key, which is not in the environment or backend/.env."""


def key(*names: str) -> str | None:
    """The first of these variables found in the environment, then in backend/.env. Values are never printed."""
    env: dict[str, str] = {}
    p = BACKEND / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    for n in names:
        found = os.environ.get(n) or env.get(n)
        if found:
            return found
    return None


def _frame(rows: list[tuple[date, float, float, float, float, float]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["Date", *COLS]).drop_duplicates("Date", keep="last")
    return df.set_index(pd.DatetimeIndex(pd.to_datetime(df.pop("Date")))).sort_index()


# ---------- parsers (no network) ----------

def parse_binance(klines: list[list[Any]]) -> pd.DataFrame:
    """[open_time_ms, open, high, low, close, volume, close_time_ms, ...] -> daily bars dated by the UTC open day."""
    return _frame([(datetime.fromtimestamp(k[0] / 1000, UTC).date(), float(k[1]), float(k[2]), float(k[3]),
                    float(k[4]), float(k[5])) for k in klines])


def parse_alpaca(bars: list[dict[str, Any]]) -> pd.DataFrame:
    """[{"t": "2026-09-24T04:00:00Z", "o", "h", "l", "c", "v"}] -> daily bars dated by the New York trading day."""
    return _frame([(pd.Timestamp(b["t"]).tz_convert("America/New_York").date(), float(b["o"]), float(b["h"]),
                    float(b["l"]), float(b["c"]), float(b["v"])) for b in bars])


def parse_polygon(results: list[dict[str, Any]]) -> pd.DataFrame:
    """[{"t": ms at 00:00 New York, "o", "h", "l", "c", "v"}] -> daily bars dated by the New York trading day."""
    return _frame([(pd.Timestamp(r["t"], unit="ms", tz="UTC").tz_convert("America/New_York").date(), float(r["o"]),
                    float(r["h"]), float(r["l"]), float(r["c"]), float(r.get("v", 0.0))) for r in results])


# ---------- fetchers ----------

def binance_daily(asset: str, start: date, end: date, client: httpx.Client | None = None) -> pd.DataFrame:
    sym = BINANCE_PAIRS.get(asset, asset)
    c = client or httpx.Client(timeout=20)
    rows: list[list[Any]] = []
    t0 = int(datetime(start.year, start.month, start.day, tzinfo=UTC).timestamp() * 1000)
    t1 = int(datetime(end.year, end.month, end.day, tzinfo=UTC).timestamp() * 1000)
    while t0 <= t1:
        r = c.get(BINANCE, params={"symbol": sym, "interval": "1d", "startTime": t0, "endTime": t1, "limit": 1000})
        r.raise_for_status()
        page = r.json()
        if not page:
            break
        rows += page
        t0 = int(page[-1][0]) + 86_400_000
    return parse_binance(rows)


def alpaca_daily(symbol: str, start: date, end: date, client: httpx.Client | None = None) -> pd.DataFrame:
    kid, sec = key("ALPACA_API_KEY_ID", "APCA_API_KEY_ID"), key("ALPACA_API_SECRET_KEY", "APCA_API_SECRET_KEY")
    if not (kid and sec):
        raise NoKeyError("Alpaca needs ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY in backend/.env (free paper account)")
    c = client or httpx.Client(timeout=20)
    bars: list[dict[str, Any]] = []
    params: dict[str, Any] = {"symbols": symbol, "timeframe": "1Day", "start": start.isoformat(),
                              "end": end.isoformat(), "feed": "iex", "adjustment": "all", "limit": 10000}
    while True:
        r = c.get(ALPACA, params=params, headers={"APCA-API-KEY-ID": kid, "APCA-API-SECRET-KEY": sec})
        r.raise_for_status()
        d = r.json()
        bars += (d.get("bars") or {}).get(symbol, [])
        if not d.get("next_page_token"):
            break
        params["page_token"] = d["next_page_token"]
    return parse_alpaca(bars)


def polygon_daily(ticker: str, start: date, end: date, client: httpx.Client | None = None) -> pd.DataFrame:
    k = key("POLYGON_API_KEY", "MASSIVE_API_KEY")
    if not k:
        raise NoKeyError("Polygon/Massive needs POLYGON_API_KEY in backend/.env (free Stocks Basic key)")
    wait = 12.5 - (time.monotonic() - _last_polygon[0])  # free plan: 5 calls a minute
    if wait > 0:
        time.sleep(wait)
    c = client or httpx.Client(timeout=20)
    r = c.get(f"{POLYGON}/v2/aggs/ticker/{ticker}/range/1/day/{start.isoformat()}/{end.isoformat()}",
              params={"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": k})
    _last_polygon[0] = time.monotonic()
    r.raise_for_status()
    return parse_polygon(r.json().get("results") or [])


def compare(a: pd.Series, b: pd.Series) -> dict[str, Any]:
    """Close-to-close agreement of two sources on their common days. `ratio_drift` flags a mixed price caliber (one
    source dividend-adjusted, the other not): the ratio then trends instead of hovering at 1."""
    j = pd.concat([a.rename("a"), b.rename("b")], axis=1).dropna()
    if j.empty:
        return {"days": 0}
    rel = (j["a"] / j["b"] - 1).abs()
    ratio = j["a"] / j["b"]
    return {"days": len(j), "median_abs_diff": float(rel.median()), "max_abs_diff": float(rel.max()),
            "worst_day": pd.Timestamp(str(rel.idxmax())).date().isoformat(),
            "ratio_drift": float(ratio.iloc[-min(20, len(ratio)):].mean() - ratio.iloc[:min(20, len(ratio))].mean()),
            "return_corr": float(j["a"].pct_change().corr(j["b"].pct_change())) if len(j) > 2 else None}


def default_window(days: int = 400) -> tuple[date, date]:
    end = datetime.now(UTC).date()
    return end - timedelta(days=days), end
