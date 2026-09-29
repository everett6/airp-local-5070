"""Cross-check Yahoo's daily closes against the other free sources (app/data_ingestion/bars.py). Run by hand.

    python scripts/price_crosscheck.py              # last 400 days: BTC/ETH vs Binance; SPY vs Alpaca and Polygon
    python scripts/price_crosscheck.py --days 90

Crypto: Yahoo's BTC-USD vs Binance's BTCUSDT (a USD vs USDT basis of a few basis points is normal). SPY: Yahoo
(dividend-adjusted) vs Alpaca's IEX feed and Polygon, both asked for adjusted bars; a source whose key is not in
backend/.env is skipped and says which variable it needs. Flags: a median gap above 0.5%, any day above 3%, or a trending
price ratio (one source adjusted, the other not). Writes results/price_crosscheck.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import httpx
import yfinance as yf

from app.data_ingestion import bars

CHECKS = {"BTC-USD": [("binance", bars.binance_daily)], "ETH-USD": [("binance", bars.binance_daily)],
          "SPY": [("alpaca-iex", bars.alpaca_daily), ("polygon", bars.polygon_daily)]}


def flags(r: dict) -> list[str]:
    out = []
    if r.get("days", 0) == 0:
        return ["no common days"]
    if r["median_abs_diff"] > 0.005:
        out.append(f"median gap {r['median_abs_diff']:.2%}")
    if r["max_abs_diff"] > 0.03:
        out.append(f"worst day {r['worst_day']} off by {r['max_abs_diff']:.2%}")
    if abs(r["ratio_drift"]) > 0.005:
        out.append(f"price ratio drifts {r['ratio_drift']:+.2%}: mixed adjustment?")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=400)
    args = ap.parse_args()
    start, end = bars.default_window(args.days)
    out = {}
    for t, sources in CHECKS.items():
        y = yf.download(t, start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(), auto_adjust=True,
                        progress=False, multi_level_index=False)
        if y.empty:
            print(f"{t}: Yahoo returned nothing")
            continue
        y.index = y.index.tz_localize(None).normalize() if y.index.tz is not None else y.index.normalize()
        for name, fn in sources:
            try:
                other = fn(t, start, end)
            except bars.NoKeyError as e:
                print(f"{t} vs {name}: skipped ({e})")
                out[f"{t}|{name}"] = {"skipped": str(e)}
                continue
            except httpx.HTTPError as e:
                print(f"{t} vs {name}: request failed ({e})")
                out[f"{t}|{name}"] = {"error": str(e)}
                continue
            last_complete = end - timedelta(days=1)  # today's bar is still forming in both sources
            r = bars.compare(y["Close"].loc[:str(last_complete)], other["Close"].loc[:str(last_complete)])
            r["flags"] = flags(r)
            out[f"{t}|{name}"] = r
            print(f"{t} vs {name}: {r['days']} days, median gap {r.get('median_abs_diff', 0):.3%}, "
                  f"worst {r.get('max_abs_diff', 0):.2%} ({r.get('worst_day')}), return corr "
                  f"{r.get('return_corr') or float('nan'):.4f}" + (f"  FLAGS: {'; '.join(r['flags'])}" if r["flags"]
                                                                  else "  ok"))
    (BACKEND / "results" / "price_crosscheck.json").write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()
