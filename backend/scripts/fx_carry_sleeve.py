"""A3: G10 currency carry against USD (free Yahoo spot + FRED/OECD 3-month rates).

    python scripts/fx_carry_sleeve.py [--cost-bps 10]

Spec and pass rules were written before the first run (docs/STRATEGY_RESEARCH.md, "A3"). Each month-end: rate
differential vs the US from the PREVIOUS month's OECD value (publication lag; a series that stops publishing keeps its
last value), long the top 3 and short the bottom 3 of AUD CAD CHF EUR GBP JPY NZD NOK SEK, equal weight, scaled to 10%
ex-ante vol (252-day covariance, gross <= 4x). Daily return per currency = USD-value spot change + differential / 252.
Standalone window 2012-01 on; adding rule against B0 2018-26. Writes results/fx_carry_sleeve<tag>.json.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import sys
import time
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np
import pandas as pd
from trend_sleeve import block_boot, master_b0, sharpe, stats

# Yahoo symbol and whether it quotes USD per unit (True) or units per USD (False)
SPOT = {"AUD": ("AUDUSD=X", True), "NZD": ("NZDUSD=X", True), "EUR": ("EURUSD=X", True), "GBP": ("GBPUSD=X", True),
        "CAD": ("CAD=X", False), "CHF": ("CHF=X", False), "JPY": ("JPY=X", False), "NOK": ("NOK=X", False),
        "SEK": ("SEK=X", False)}
RATE = {"AUD": "AU", "NZD": "NZ", "EUR": "EZ", "GBP": "GB", "CAD": "CA", "CHF": "CH", "JPY": "JP", "NOK": "NO",
        "SEK": "SE", "USD": "US"}
DATA = BACKEND / "data" / "fx"


def fred(code: str) -> pd.Series:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id=IR3TIB01{code}M156N"
    with urllib.request.urlopen(url, timeout=60) as f:
        df = pd.read_csv(io.StringIO(f.read().decode()))
    s = pd.to_numeric(df.iloc[:, 1], errors="coerce")
    return pd.Series(s.to_numpy() / 100, index=pd.to_datetime(df.iloc[:, 0])).dropna()


def load(end: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    DATA.mkdir(parents=True, exist_ok=True)
    spot_f, rate_f = DATA / "spot_usd_value.parquet", DATA / "rates_3m.parquet"
    if not spot_f.exists():
        import yfinance as yf
        cols = {}
        for ccy, (sym, usd_per) in SPOT.items():
            for _ in range(3):
                s = yf.download(sym, start="2004-01-01", end=end, auto_adjust=True, progress=False,
                                multi_level_index=False)["Close"].dropna()
                if len(s):
                    break
                time.sleep(5)
            if not len(s):
                raise SystemExit(f"no spot data for {sym}")
            cols[ccy] = s if usd_per else 1.0 / s
        pd.DataFrame(cols).to_parquet(spot_f)
    if not rate_f.exists():
        pd.DataFrame({ccy: fred(code) for ccy, code in RATE.items()}).to_parquet(rate_f)
    return pd.read_parquet(spot_f), pd.read_parquet(rate_f)


def sleeve_returns(spot: pd.DataFrame, rates: pd.DataFrame, cost_bps: float, target_vol: float = 0.10,
                   gross_cap: float = 4.0) -> tuple[pd.Series, pd.DataFrame]:
    spot = spot.sort_index().ffill()
    days = spot.index
    diff_m = rates.drop(columns="USD").sub(rates["USD"], axis=0).sort_index().ffill()
    # value for month M is known in month M+1: shift by one month; then carry daily
    known = diff_m.shift(1, freq="MS").reindex(days, method="ffill")
    r = spot.pct_change(fill_method=None) + known / 252
    month_ends = days.to_series().groupby(days.to_period("M")).last()
    weights = {}
    for t in month_ends:
        i = days.get_loc(t)
        if i < 252 or known.loc[t].isna().any():
            continue
        d = known.loc[t].sort_values()
        w = pd.Series(0.0, index=spot.columns)
        w[d.index[-3:]] = 1 / 3
        w[d.index[:3]] = -1 / 3
        cov = r.iloc[i - 251:i + 1].cov() * 252
        ex_ante = math.sqrt(float(w @ cov @ w))
        w = w * target_vol / ex_ante
        if w.abs().sum() > gross_cap:
            w *= gross_cap / w.abs().sum()
        weights[t] = w
    W = pd.DataFrame(weights).T.sort_index()
    daily_w = W.reindex(days).ffill().shift(1).dropna(how="all")
    gross = (daily_w * r.loc[daily_w.index].fillna(0.0)).sum(axis=1)
    turnover = W.diff().abs().sum(axis=1)
    turnover.iloc[0] = W.iloc[0].abs().sum()
    cost = pd.Series(0.0, index=daily_w.index)
    for t, tv in turnover.items():
        nxt = daily_w.index[daily_w.index > t]
        if len(nxt):
            cost[nxt[0]] += tv * cost_bps / 1e4
    return (gross - cost).rename("fx_carry"), W


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--end", default="2026-09-26")
    ap.add_argument("--cost-bps", type=float, default=10.0)
    ap.add_argument("--tag", default="")
    args = ap.parse_args()
    spot, rates = load(args.end)
    fx, W = sleeve_returns(spot, rates, args.cost_bps)
    post = fx.loc["2012-01-01":]
    ci = np.percentile(block_boot([post.to_numpy()], sharpe), [2.5, 97.5])
    out = {"cost_bps": args.cost_bps, "full": stats(fx), "post_2012": stats(post),
           "post_2012_sharpe_ci95": [round(float(x), 2) for x in ci],
           "turnover_per_year": round(float(W.diff().abs().sum(axis=1).mean() * 12), 2),
           "avg_gross": round(float(W.abs().sum(axis=1).mean()), 2), "standalone_pass": bool(ci[0] > 0),
           "rates_last": {c: str(rates[c].dropna().index[-1].date()) for c in rates}}
    b0 = master_b0("2018-01-02", args.end)
    b0.index = pd.to_datetime(b0.index).normalize()
    fx.index = pd.to_datetime(fx.index).tz_localize(None).normalize()
    both = pd.concat([b0, fx], axis=1, join="inner").dropna()
    b1 = both["B0"] + both["fx_carry"]
    scaled = b1 * (both["B0"].std() / b1.std())
    d = block_boot([both["B0"].to_numpy(), b1.to_numpy()], lambda a, b: sharpe(b) - sharpe(a))
    lo, hi = np.percentile(d, [5, 95])
    s0, s1, s1v = stats(both["B0"]), stats(b1), stats(scaled)
    out["book"] = {"B0": s0, "B1": s1, "B1_vol_matched": s1v, "days": len(both),
                   "corr_sleeve_B0": round(float(both["B0"].corr(both["fx_carry"])), 2),
                   "sharpe_diff": round(sharpe(b1.to_numpy()) - sharpe(both["B0"].to_numpy()), 2),
                   "sharpe_diff_ci90": [round(float(lo), 2), round(float(hi), 2)]}
    out["adds_value_pass"] = bool(s1v["cagr_pct"] > s0["cagr_pct"] and lo > 0)
    path = BACKEND / "results" / f"fx_carry_sleeve{args.tag}.json"
    path.write_text(json.dumps(out, indent=1))
    print(f"FX carry @ {args.cost_bps:g} bps  turnover {out['turnover_per_year']}x/yr  avg gross {out['avg_gross']}x  "
          f"rates last {out['rates_last']}")
    for name, s in (("full", out["full"]), ("2012-26", out["post_2012"])):
        print(f"  {name} {s['start']}..: CAGR {s['cagr_pct']}%  vol {s['vol_pct']}%  Sharpe {s['sharpe']}  maxDD "
              f"{s['max_drawdown_pct']}%  worst month {s['worst_month_pct']}%  worst year {s['worst_year_pct']}%")
    print(f"  2012-26 Sharpe 95% CI {out['post_2012_sharpe_ci95']} -> standalone "
          f"{'PASS' if out['standalone_pass'] else 'FAIL'}")
    b = out["book"]
    for name in ("B0", "B1", "B1_vol_matched"):
        s = b[name]
        print(f"  {name:15s} CAGR {s['cagr_pct']}%  vol {s['vol_pct']}%  Sharpe {s['sharpe']}  maxDD {s['max_drawdown_pct']}%")
    print(f"  days {b['days']} corr {b['corr_sleeve_B0']}  Sharpe B1-B0 {b['sharpe_diff']} 90% CI {b['sharpe_diff_ci90']} "
          f"-> adds value {'PASS' if out['adds_value_pass'] else 'FAIL'}")
    print(f"  yearly: {out['full']['yearly_pct']}")


if __name__ == "__main__":
    main()
