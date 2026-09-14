"""Build a point-in-time stock universe, avoiding survivorship bias.

    python scripts/build_universe.py --as-of 2025-06-02 --top 20 --end 2026-09-12

Rule, using only information available on the as-of date:
  1. S&P 500 membership on that date: the constituents table from the last
     Wikipedia revision before the as-of date (a dated, citable snapshot).
  2. Rank members by average daily dollar volume (close x volume) over the
     252 trading days before the as-of date (at least 200 days required).
  3. Keep the top N.

Writes
  data/universe_<as_of>.csv          rank, ticker, name, sector, avg dollar volume
  data/universe_<as_of>.meta.json    sources, counts, tickers without data
  data/prices_pit_<as_of>.csv        daily closes for the universe + SPY (--end required)

Caveat recorded in the meta file: members that later delisted may have no Yahoo
data and so can't be ranked. They're listed so their impact can be judged.
"""
from __future__ import annotations

import argparse
import io
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
UA = "airp-local-5070/0.2 (personal research; +https://github.com/everett6/airp-local-5070)"


def membership_as_of(as_of: date) -> tuple[pd.DataFrame, dict[str, Any]]:
    """The constituents table exactly as Wikipedia showed it on the as-of date (last revision before it)."""
    api = "https://en.wikipedia.org/w/api.php"
    params = {"action": "query", "prop": "revisions", "titles": "List of S&P 500 companies", "rvlimit": "1",
              "rvstart": f"{as_of.isoformat()}T00:00:00Z", "rvdir": "older", "rvprop": "ids|timestamp",
              "format": "json"}
    info = httpx.get(api, params=params, headers={"User-Agent": UA}, timeout=30).json()
    rev = next(iter(info["query"]["pages"].values()))["revisions"][0]
    html = httpx.get("https://en.wikipedia.org/w/index.php", params={"oldid": rev["revid"]},
                     headers={"User-Agent": UA}, timeout=30, follow_redirects=True).text
    table = pd.read_html(io.StringIO(html))[0]
    df = table.rename(columns={"Symbol": "ticker", "Security": "name", "GICS Sector": "sector"})
    df = df[["ticker", "name", "sector"]].dropna(subset=["ticker"])
    df["yahoo"] = df["ticker"].str.replace(".", "-", regex=False)
    return df, {"wikipedia_revision": rev["revid"], "revision_timestamp": rev["timestamp"],
                "url": f"https://en.wikipedia.org/w/index.php?oldid={rev['revid']}"}


def rank_by_dollar_volume(members: pd.DataFrame, as_of: date, top: int) -> tuple[pd.DataFrame, list[str], float]:
    import yfinance as yf

    start = as_of - timedelta(days=400)
    raw = yf.download(list(members["yahoo"]), start=start.isoformat(), end=as_of.isoformat(),
                      auto_adjust=False, progress=False, threads=True)
    dv = (raw["Close"] * raw["Volume"]).tail(252)
    # batch downloads drop some symbols transiently; retry those one at a time
    for sym in [s for s in dv.columns if dv[s].notna().sum() < 200]:
        one = yf.download(sym, start=start.isoformat(), end=as_of.isoformat(), auto_adjust=False,
                          progress=False)
        if len(one):
            dv[sym] = (one["Close"].squeeze() * one["Volume"].squeeze()).reindex(dv.index)
    days = dv.notna().sum()
    avg = dv.mean()
    ranked = pd.DataFrame({"yahoo": avg.index, "avg_dollar_volume": avg.values, "days": days.values})
    missing = sorted(ranked.loc[ranked["days"] < 200, "yahoo"].tolist())
    ranked = ranked[ranked["days"] >= 200].merge(members, on="yahoo").sort_values("avg_dollar_volume",
                                                                                  ascending=False)
    # one share class per company (e.g. GOOGL/GOOG): keep the more traded class
    ranked["company"] = ranked["name"].str.replace(r"\s*\(.*\)\s*$", "", regex=True).str.strip()
    ranked = ranked.drop_duplicates("company", keep="first")
    ranked["rank"] = range(1, len(ranked) + 1)
    return ranked.head(top), missing, float(ranked["avg_dollar_volume"].iloc[top - 1])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", required=True)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--end", required=True, help="last price date to download (exclusive), e.g. 2026-09-12")
    ap.add_argument("--price-start", default="2024-01-01")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()
    as_of = date.fromisoformat(args.as_of)
    # the original top-20 files keep their names; other sizes get a suffix so nothing frozen is overwritten
    suffix = "" if args.top == 20 else f"_top{args.top}"
    for existing in (BACKEND / "data" / f"universe_{as_of}{suffix}.csv", BACKEND / "data" / f"prices_pit_{as_of}{suffix}.csv"):
        if existing.exists() and not args.overwrite:
            raise SystemExit(f"{existing.name} exists (a frozen run may depend on it); pass --overwrite to replace it")
    members, snapshot = membership_as_of(as_of)
    top, missing, cutoff_dv = rank_by_dollar_volume(members, as_of, args.top)
    data = BACKEND / "data"
    data.mkdir(exist_ok=True)
    out = top.assign(avg_dollar_volume_usd_bn=(top["avg_dollar_volume"] / 1e9).round(2))
    out[["rank", "yahoo", "name", "sector", "avg_dollar_volume_usd_bn", "days"]].rename(
        columns={"yahoo": "ticker"}).to_csv(data / f"universe_{as_of}{suffix}.csv", index=False)

    import yfinance as yf

    tickers = [*out["yahoo"], "SPY"]
    px = yf.download(tickers, start=args.price_start, end=args.end, auto_adjust=True, progress=False)["Close"]
    px = px.reindex(columns=tickers)
    # batch downloads drop whole symbols transiently (seen: NVDA, AAPL); retry any symbol with >10% gaps
    for sym in [s for s in tickers if px[s].isna().mean() > 0.10]:
        for _ in range(3):
            one = yf.download(sym, start=args.price_start, end=args.end, auto_adjust=True, progress=False)
            if len(one):
                px[sym] = one["Close"].squeeze().reindex(px.index)
                break
    gaps = {t: int(px[t].isna().sum()) for t in tickers if px[t].isna().any()}
    px = px.dropna(subset=["SPY"])
    px.index = px.index.strftime("%Y-%m-%d")
    px.to_csv(data / f"prices_pit_{as_of}{suffix}.csv")
    meta = {
        "as_of": str(as_of), "rule": f"S&P 500 members on as_of, top {args.top} by mean daily dollar volume over "
                                     "the prior 252 trading days (min 200)",
        "sources": {"membership": snapshot["url"], "prices": "Yahoo Finance via yfinance"},
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "members_on_as_of": len(members), "membership_snapshot": snapshot,
        "members_without_enough_data": missing, "price_gaps_after_download": gaps,
        "rank_cutoff_avg_dollar_volume_usd_bn": round(cutoff_dv / 1e9, 2),
        "dedupe": "one share class per company (the more traded one)",
        "universe": list(out["yahoo"]), "price_file": f"prices_pit_{as_of}{suffix}.csv",
        "price_window": [args.price_start, args.end],
    }
    (data / f"universe_{as_of}{suffix}.meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(json.dumps({k: meta[k] for k in ("members_on_as_of", "membership_snapshot", "universe",
                                           "price_gaps_after_download")}, indent=1))
    print("members without enough data:", len(missing), missing[:15], "| #N cutoff $bn/day:",
          meta["rank_cutoff_avg_dollar_volume_usd_bn"])


if __name__ == "__main__":
    main()
