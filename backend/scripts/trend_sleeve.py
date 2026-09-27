"""Sleeve 1: diversified trend following on 20 ETFs, and whether it improves the master book.

    python scripts/trend_sleeve.py [--cost-bps 10]

Spec and pass rules were written before the first run (docs/STRATEGY_RESEARCH.md, "Sleeve 1"):
month-end sign of the 252-day return in excess of BIL, weight = sign / 63-day vol, book scaled to 10% ex-ante vol
(252-day covariance, gross <= 4x); weights set at close t earn returns from t+1 (held at target between month-ends).
Returns are in excess of BIL, minus costs x |weight change| at each rebalance, 1.5%/yr on long gross above 1x and
0.5%/yr on short gross. B0 = master_portfolio.py crypto (SPY core + crypto sleeve); B1 = B0 + this sleeve as an
overlay. Writes results/trend_sleeve<tag>.json and prints the verdicts.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np
import pandas as pd

ETFS = ["SPY", "QQQ", "IWM", "EFA", "EEM", "EWJ", "TLT", "IEF", "LQD", "HYG", "TIP", "GLD", "SLV", "USO", "DBC",
        "DBA", "UUP", "FXE", "FXY", "VNQ"]
CASH = "BIL"
DATA = BACKEND / "data" / "trend" / "etf_closes.parquet"


def load_closes(end: str) -> pd.DataFrame:
    if not DATA.exists():
        import yfinance as yf
        DATA.parent.mkdir(parents=True, exist_ok=True)
        df = yf.download(ETFS + [CASH], start="2006-01-01", end=end, auto_adjust=True, progress=False)["Close"]
        for _ in range(3):  # Yahoo sometimes drops a ticker from a batch: fetch the missing ones again
            missing = [c for c in ETFS + [CASH] if c not in df or df[c].isna().all()]
            if not missing:
                break
            time.sleep(5)
            again = yf.download(missing, start="2006-01-01", end=end, auto_adjust=True, progress=False)["Close"]
            df = df.drop(columns=[c for c in missing if c in df]).join(again, how="outer")
        missing = [c for c in ETFS + [CASH] if c not in df or df[c].isna().all()]
        if missing:
            raise SystemExit(f"no data for {missing}; not writing a partial universe")
        df.index = pd.to_datetime(df.index)
        df.to_parquet(DATA)
    return pd.read_parquet(DATA)


def sleeve_returns(closes: pd.DataFrame, cost_bps: float, target_vol: float = 0.10,
                   gross_cap: float = 4.0) -> tuple[pd.Series, pd.DataFrame]:
    r = closes.pct_change()
    ex = r[ETFS].sub(r[CASH], axis=0)
    month_ends = closes.index.to_series().groupby(closes.index.to_period("M")).last()
    weights: dict[pd.Timestamp, pd.Series] = {}
    for t in month_ends:
        i = closes.index.get_loc(t)
        if i < 252:
            continue
        p0, p1 = closes.iloc[i - 252], closes.iloc[i]
        ok = [a for a in ETFS if pd.notna(p0[a]) and pd.notna(p1[a]) and ex[a].iloc[i - 251:i + 1].notna().all()]
        if len(ok) < 5 or pd.isna(p0[CASH]):
            continue
        trend = (p1[ok] / p0[ok] - 1) - (p1[CASH] / p0[CASH] - 1)
        vol = r[ok].iloc[i - 62:i + 1].std() * math.sqrt(252)
        raw = np.sign(trend) / vol
        cov = ex[ok].iloc[i - 251:i + 1].cov() * 252
        ex_ante = math.sqrt(float(raw @ cov @ raw))
        w = raw * target_vol / ex_ante
        if w.abs().sum() > gross_cap:
            w *= gross_cap / w.abs().sum()
        weights[t] = w.reindex(ETFS).fillna(0.0)
    W = pd.DataFrame(weights).T.sort_index()
    daily_w = W.reindex(closes.index).ffill().shift(1)  # weights set at close t apply from t+1
    daily_w = daily_w.loc[daily_w.dropna(how="all").index]
    gross = (daily_w * ex.loc[daily_w.index]).sum(axis=1)
    longs = daily_w.clip(lower=0).sum(axis=1)
    shorts = -daily_w.clip(upper=0).sum(axis=1)
    financing = (0.015 * (longs - 1).clip(lower=0) + 0.005 * shorts) / 252
    turnover = W.diff().abs().sum(axis=1)
    turnover.iloc[0] = W.iloc[0].abs().sum()
    cost = pd.Series(0.0, index=daily_w.index)
    for t, tv in turnover.items():  # charged on the first day the new weights earn
        nxt = daily_w.index[daily_w.index > t]
        if len(nxt):
            cost[nxt[0]] += tv * cost_bps / 1e4
    return (gross - financing - cost).rename("trend"), W


def stats(r: pd.Series) -> dict[str, float]:
    r = r.dropna()
    eq = (1 + r).cumprod()
    years = len(r) / 252
    monthly = (1 + r).groupby(r.index.to_period("M")).prod() - 1
    yearly = (1 + r).groupby(r.index.year).prod() - 1
    return {"start": str(r.index[0].date()), "end": str(r.index[-1].date()),
            "cagr_pct": round(100 * (eq.iloc[-1] ** (1 / years) - 1), 2),
            "vol_pct": round(100 * r.std() * math.sqrt(252), 2),
            "sharpe": round(float(r.mean() / r.std() * math.sqrt(252)), 2),
            "max_drawdown_pct": round(100 * float((1 - eq / eq.cummax()).max()), 1),
            "worst_month_pct": round(100 * float(monthly.min()), 1), "worst_year_pct": round(100 * float(yearly.min()), 1),
            "yearly_pct": {int(y): round(100 * float(v), 1) for y, v in yearly.items()}}


def sharpe(x: np.ndarray) -> float:
    return float(x.mean() / x.std() * math.sqrt(252)) if x.std() > 0 else 0.0


def block_boot(cols: list[np.ndarray], fn, reps: int = 2000, block: int = 63, seed: int = 0) -> np.ndarray:
    """Circular block bootstrap over days (63-day = 3-month blocks), resampling all series with the same blocks."""
    rng = np.random.default_rng(seed)
    n = len(cols[0])
    k = math.ceil(n / block)
    out = np.empty(reps)
    for j in range(reps):
        idx = (rng.integers(0, n, k)[:, None] + np.arange(block)[None, :]).ravel()[:n] % n
        out[j] = fn(*(c[idx] for c in cols))
    return out


def master_b0(start: str, end: str) -> pd.Series:
    from master_portfolio import load_crypto
    from app.portfolio.master import MasterConfig, allocate, crypto_state, simulate_weights
    from app.sandbox.events import Prices
    px = Prices.from_long(load_crypto(end))
    cfg = MasterConfig()
    days = [d for d in px.close.index if d.date() >= date.fromisoformat(start)]
    targets = {d.date(): allocate([], crypto_state(px.close, d, cfg.crypto_assets), cfg).weights for d in days[::5]}
    sim = simulate_weights(targets, px.open, px.close, days[0].date(), days[-1].date())
    eq = pd.Series(sim.equity, index=pd.to_datetime(sim.days))
    return eq.pct_change().dropna().rename("B0")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--end", default="2026-09-26")
    ap.add_argument("--cost-bps", type=float, default=10.0)
    ap.add_argument("--tag", default="")
    args = ap.parse_args()
    closes = load_closes(args.end)
    trend, W = sleeve_returns(closes, args.cost_bps)
    full = trend.loc["2008-01-01":]
    post = trend.loc["2013-01-01":]
    ci = np.percentile(block_boot([post.to_numpy()], sharpe), [2.5, 97.5])
    out = {"cost_bps": args.cost_bps, "full_2008": stats(full), "post_2013": stats(post),
           "post_2013_sharpe_ci95": [round(float(x), 2) for x in ci],
           "turnover_per_year": round(float(W.diff().abs().sum(axis=1).mean() * 12), 2),
           "avg_gross": round(float(W.abs().sum(axis=1).mean()), 2)}
    out["standalone_pass"] = bool(ci[0] > 0)

    b0 = master_b0("2018-01-02", args.end)
    both = pd.concat([b0, trend], axis=1, join="inner").dropna()
    b1 = both["B0"] + both["trend"]
    scaled = b1 * (both["B0"].std() / b1.std())
    d = block_boot([both["B0"].to_numpy(), b1.to_numpy()], lambda a, b: sharpe(b) - sharpe(a))
    lo, hi = np.percentile(d, [5, 95])
    s0, s1, s1v = stats(both["B0"]), stats(b1), stats(scaled)
    out["book"] = {"B0": s0, "B1": s1, "B1_vol_matched": s1v,
                   "corr_trend_B0": round(float(both["B0"].corr(both["trend"])), 2),
                   "sharpe_diff": round(sharpe(b1.to_numpy()) - sharpe(both["B0"].to_numpy()), 2),
                   "sharpe_diff_ci90": [round(float(lo), 2), round(float(hi), 2)]}
    out["adds_value_pass"] = bool(s1v["cagr_pct"] > s0["cagr_pct"] and lo > 0)

    path = BACKEND / "results" / f"trend_sleeve{args.tag}.json"
    path.write_text(json.dumps(out, indent=1))
    f, p = out["full_2008"], out["post_2013"]
    print(f"trend sleeve @ {args.cost_bps:g} bps  turnover {out['turnover_per_year']}x/yr  avg gross {out['avg_gross']}x")
    for name, s in (("2008-26", f), ("2013-26", p)):
        print(f"  {name}: excess CAGR {s['cagr_pct']}%  vol {s['vol_pct']}%  Sharpe {s['sharpe']}  maxDD "
              f"{s['max_drawdown_pct']}%  worst month {s['worst_month_pct']}%  worst year {s['worst_year_pct']}%")
    print(f"  2013-26 Sharpe 95% CI {out['post_2013_sharpe_ci95']} -> standalone {'PASS' if out['standalone_pass'] else 'FAIL'}")
    b = out["book"]
    for name in ("B0", "B1", "B1_vol_matched"):
        s = b[name]
        print(f"  {name:15s} {s['start']}..{s['end']}: CAGR {s['cagr_pct']}%  vol {s['vol_pct']}%  Sharpe {s['sharpe']}  "
              f"maxDD {s['max_drawdown_pct']}%")
    print(f"  corr(trend, B0) {b['corr_trend_B0']}  Sharpe B1-B0 {b['sharpe_diff']} 90% CI {b['sharpe_diff_ci90']} -> "
          f"adds value {'PASS' if out['adds_value_pass'] else 'FAIL'}")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
