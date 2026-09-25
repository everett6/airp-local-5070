"""Build a 16-year point-in-time universe and daily OHLCV bars from free sources (Wikipedia + Yahoo).

    python scripts/build_history.py --first-year 2010 --last-year 2026 --top 100 --end 2026-09-24

For each year Y, using only what was known on the first day of Y:
  1. S&P 500 members: the constituents table from the last Wikipedia revision before Jan 1 of Y.
  2. Rank those members by mean daily dollar volume over the prior 252 trading days (min 200 days).
  3. Keep the top N (one share class per company).
Stocks are held in the universe for that calendar year only; the next year is re-ranked from scratch.

Writes (refuses to overwrite)
  data/hist/universe_<first>_<last>_top<N>.csv       year, as_of, rank, ticker, name, sector, avg $vol
  data/hist/ohlcv_<first>_<last>_top<N>.parquet      Date, Ticker, Open, High, Low, Close, Volume (+ SPY)
  data/hist/universe_<first>_<last>_top<N>.meta.json  Wikipedia revisions, tickers Yahoo no longer has, sha256s

Known residual survivorship bias, recorded per year in the meta file: members that were later
delisted, acquired or renamed are often missing from Yahoo, so they can't be ranked or held.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import yfinance as yf

BACKEND = Path(__file__).resolve().parents[1]
UA = "airp-local-5070/0.2 (personal research; +https://github.com/everett6/airp-local-5070)"
# Renamed tickers whose full history Yahoo now keeps under the new symbol. Same company, same shares.
RENAMED = {"FB": "META", "ANTM": "ELV", "RE": "EG", "PKI": "RVTY", "FLT": "CPAY", "WLTW": "WTW", "ABC": "COR",
           "CTL": "LUMN", "FISV": "FI", "PEAK": "DOC", "HCP": "DOC", "BLL": "BALL", "COG": "CTRA", "TMK": "GL",
           "CBS": "PARA", "VIAC": "PARA", "SYMC": "GEN", "NLOK": "GEN", "KORS": "CPRI", "HRS": "LHX",
           "ADS": "BFH", "DISCA": "WBD", "GPS": "GAP"}


def membership_as_of(as_of: date) -> tuple[pd.DataFrame, dict[str, Any]]:
    api = "https://en.wikipedia.org/w/api.php"
    params = {"action": "query", "prop": "revisions", "titles": "List of S&P 500 companies", "rvlimit": "1",
              "rvstart": f"{as_of.isoformat()}T00:00:00Z", "rvdir": "older", "rvprop": "ids|timestamp",
              "format": "json"}
    info = httpx.get(api, params=params, headers={"User-Agent": UA}, timeout=30).json()
    rev = next(iter(info["query"]["pages"].values()))["revisions"][0]
    html = httpx.get("https://en.wikipedia.org/w/index.php", params={"oldid": rev["revid"]},
                     headers={"User-Agent": UA}, timeout=30, follow_redirects=True).text
    table = pd.read_html(io.StringIO(html))[0]
    cols = {c: c for c in table.columns}
    for c in table.columns:
        lc = str(c).lower()
        if lc in ("symbol", "ticker symbol", "ticker"):
            cols[c] = "ticker"
        elif lc in ("security", "company"):
            cols[c] = "name"
        elif lc == "gics sector":
            cols[c] = "sector"
    df = table.rename(columns=cols)[["ticker", "name", "sector"]].dropna(subset=["ticker"])
    df["ticker"] = df["ticker"].astype(str).str.strip()
    df["yahoo"] = df["ticker"].map(lambda t: RENAMED.get(t, t)).str.replace(".", "-", regex=False)
    return df, {"wikipedia_revision": rev["revid"], "revision_timestamp": rev["timestamp"], "members": len(df),
                "url": f"https://en.wikipedia.org/w/index.php?oldid={rev['revid']}"}


def download(tickers: list[str], start: date, end: date) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for i in range(0, len(tickers), 50):
        chunk = tickers[i:i + 50]
        raw = yf.download(chunk, start=start.isoformat(), end=end.isoformat(), auto_adjust=True, progress=False,
                          threads=True, group_by="ticker")
        for t in chunk:
            try:
                df = raw[t][["Open", "High", "Low", "Close", "Volume"]].dropna()
            except KeyError:
                continue
            if len(df):
                out[t] = df
        print(f"downloaded {min(i + 50, len(tickers))}/{len(tickers)}", flush=True)
    for t in [t for t in tickers if t not in out]:  # batch downloads drop symbols transiently: retry alone
        for attempt in range(2):
            df = yf.download(t, start=start.isoformat(), end=end.isoformat(), auto_adjust=True, progress=False,
                             multi_level_index=False)
            if len(df):
                out[t] = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
                break
            time.sleep(1 + attempt)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--first-year", type=int, default=2010)
    ap.add_argument("--last-year", type=int, default=2026)
    ap.add_argument("--top", type=int, default=100)
    ap.add_argument("--end", required=True, help="last price date to download (exclusive)")
    args = ap.parse_args()
    folder = BACKEND / "data" / "hist"
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{args.first_year}_{args.last_year}_top{args.top}"
    uni_path, px_path = folder / f"universe_{stem}.csv", folder / f"ohlcv_{stem}.parquet"
    meta_path = folder / f"universe_{stem}.meta.json"
    for p in (uni_path, px_path):
        if p.exists():
            raise SystemExit(f"{p} exists; delete it first if you really mean to rebuild")

    years = list(range(args.first_year, args.last_year + 1))
    members = {}
    snapshots = {}
    for y in years:
        members[y], snapshots[y] = membership_as_of(date(y, 1, 1))
        print(y, snapshots[y]["revision_timestamp"], snapshots[y]["members"], "members", flush=True)
    every = sorted({t for m in members.values() for t in m["yahoo"]} | {"SPY"})
    bars = download(every, date(args.first_year - 1, 1, 1), date.fromisoformat(args.end))

    rows, per_year = [], {}
    for y in years:
        as_of = pd.Timestamp(date(y, 1, 1))
        m = members[y]
        ranked = []
        missing = []
        for _, r in m.iterrows():
            df = bars.get(r["yahoo"])
            hist = df.loc[df.index < as_of].tail(252) if df is not None else None
            if hist is None or len(hist) < 200:
                missing.append(r["ticker"])
                continue
            ranked.append({"ticker": r["yahoo"], "name": r["name"], "sector": r["sector"],
                           "avg_dollar_volume": float((hist["Close"] * hist["Volume"]).mean())})
        rk = pd.DataFrame(ranked).sort_values("avg_dollar_volume", ascending=False)
        rk["company"] = rk["name"].astype(str).str.replace(r"\s*\(.*\)\s*$", "", regex=True).str.replace(
            r"\s+Class [A-C]$", "", regex=True).str.strip()
        rk = rk.drop_duplicates("company").drop_duplicates("ticker").head(args.top)
        rk.insert(0, "rank", range(1, len(rk) + 1))
        rk.insert(0, "as_of", date(y, 1, 1).isoformat())
        rk.insert(0, "year", y)
        rows.append(rk.drop(columns="company"))
        per_year[y] = {**snapshots[y], "members_without_yahoo_history": sorted(missing),
                       "n_missing": len(missing), "rank_cutoff_usd_mn": round(rk["avg_dollar_volume"].iloc[-1] / 1e6)}
        print(y, "missing from Yahoo:", len(missing), flush=True)
    uni = pd.concat(rows)
    uni["avg_dollar_volume_usd_mn"] = (uni.pop("avg_dollar_volume") / 1e6).round(1)
    uni.to_csv(uni_path, index=False)

    keep = sorted(set(uni["ticker"]) | {"SPY"})
    long = pd.concat([bars[t].assign(Ticker=t) for t in keep]).rename_axis("Date").reset_index()
    long["Date"] = pd.to_datetime(long["Date"]).dt.date.astype(str)
    long = long[["Date", "Ticker", "Open", "High", "Low", "Close", "Volume"]].sort_values(["Ticker", "Date"])
    long.to_parquet(px_path, index=False)
    meta = {"rule": f"each Jan 1: S&P 500 members (Wikipedia revision before that date), top {args.top} by mean "
                    "daily dollar volume over the prior 252 trading days (min 200), one class per company",
            "sources": {"membership": "Wikipedia 'List of S&P 500 companies' revisions",
                        "prices": "Yahoo Finance via yfinance, auto_adjust=True"},
            "renamed_ticker_map": RENAMED, "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "price_window": [date(args.first_year - 1, 1, 1).isoformat(), args.end],
            "tickers_ever_in_universe": len(keep) - 1, "rows": len(long),
            "sha256": {uni_path.name: hashlib.sha256(uni_path.read_bytes()).hexdigest(),
                       px_path.name: hashlib.sha256(px_path.read_bytes()).hexdigest()},
            "years": {str(y): v for y, v in per_year.items()}}
    meta_path.write_text(json.dumps(meta, indent=1) + "\n")
    print(json.dumps({k: meta[k] for k in ("tickers_ever_in_universe", "rows", "sha256")}, indent=1))
    print({y: v["n_missing"] for y, v in per_year.items()})


if __name__ == "__main__":
    main()
