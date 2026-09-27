"""Daily statistical arbitrage, arm A: sector-residual 5-day reversal on the point-in-time top-100 universe.

    python scripts/statarb_sleeve.py --window dev        # 2010-2017 bug check (no parameter may change after it)
    python scripts/statarb_sleeve.py --window holdout    # 2018-2026-09, opened once; pass rules applied

Spec and pass rules: docs/PLAN_STATARB.md (written before any run). Residual = r - beta * r_sector (beta from a 60-day
regression ending t-1); signal = -(5-day residual sum) / 60-day residual vol, z-scored across the day's universe;
long the 10 highest / short the 10 lowest, dollar-neutral, each hedged with -w*beta in its sector ETF; formed at close
t, entered at the t+1 open, held 5 days as five overlapping books (open-to-open returns); scaled to 10% vol on the
trailing 126 days, gross <= 3x; costs per side on turnover, 0.5%/yr borrow on short gross. Also writes the candidate
list that arm B (the no-news filter) will label: results/statarb_candidates.csv.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np
import pandas as pd
from trend_sleeve import block_boot, master_b0, sharpe, stats

from app.sandbox.dsr import deflated_sharpe, load_registry, register

HIST = BACKEND / "data" / "hist"
ETF_FILE = BACKEND / "data" / "statarb" / "sector_etfs.parquet"
SECTOR_ETF = {"Information Technology": "XLK", "Financials": "XLF", "Health Care": "XLV",
              "Consumer Discretionary": "XLY", "Consumer Staples": "XLP", "Energy": "XLE", "Industrials": "XLI",
              "Materials": "XLB", "Utilities": "XLU", "Real Estate": "XLRE", "Communication Services": "XLC"}
ETF_START = {"XLRE": "2015-10-08", "XLC": "2018-06-19"}  # before these dates the hedge is SPY
N_SIDE, HOLD, LOOK, BETA_WIN, VOL_WIN = 10, 5, 5, 60, 126
WINDOWS = {"dev": ("2010-01-01", "2017-12-31"), "holdout": ("2018-01-01", "2026-09-30")}


def load_etfs(end: str) -> pd.DataFrame:
    if not ETF_FILE.exists():
        import yfinance as yf
        want = sorted(set(SECTOR_ETF.values()) | {"SPY"})
        ETF_FILE.parent.mkdir(parents=True, exist_ok=True)
        frames = {}
        for t in want:
            for _ in range(3):
                d = yf.download(t, start="2009-01-01", end=end, auto_adjust=True, progress=False,
                                multi_level_index=False)
                if len(d):
                    break
                time.sleep(5)
            if not len(d):
                raise SystemExit(f"no data for {t}; not writing a partial ETF set")
            frames[(t, "Open")], frames[(t, "Close")] = d["Open"], d["Close"]
        pd.DataFrame(frames).to_parquet(ETF_FILE)
    df = pd.read_parquet(ETF_FILE)
    df.index = pd.to_datetime(df.index)
    return df


def build(end: str, cost_bps: float, drop: set[tuple[str, str]] | None = None):
    """drop: (ISO date, ticker) candidates removed after selection (arm B); their side's weight is re-split."""
    px = pd.read_parquet(HIST / "ohlcv_2010_2026_top100.parquet")
    px["Date"] = pd.to_datetime(px["Date"])
    close = px.pivot(index="Date", columns="Ticker", values="Close").sort_index()
    opn = px.pivot(index="Date", columns="Ticker", values="Open").sort_index()
    etf = load_etfs(end)
    days = etf[("SPY", "Close")].dropna().index.intersection(close.index)
    close, opn, etf = close.reindex(days), opn.reindex(days), etf.reindex(days)
    uni = pd.read_csv(HIST / "universe_2010_2026_top100.csv")
    uni["ticker"] = uni["ticker"].str.replace(".", "-", regex=False)
    members = {int(y): set(g["ticker"]) for y, g in uni.groupby("year")}
    sector = uni.sort_values("year").groupby("ticker")["sector"].last()
    tickers = [t for t in close.columns if t in sector.index and t != "SPY"]
    close, opn = close[tickers], opn[tickers]

    # the hedge ETF each stock uses on each day (SPY before XLRE/XLC existed)
    hedge = pd.DataFrame("SPY", index=days, columns=tickers)
    for t in tickers:
        e = SECTOR_ETF.get(sector[t], "SPY")
        ok = days >= pd.Timestamp(ETF_START.get(e, "2000-01-01"))
        hedge.loc[ok, t] = e
    etf_c = {e: etf[(e, "Close")] for e in set(SECTOR_ETF.values()) | {"SPY"}}
    etf_o = {e: etf[(e, "Open")] for e in etf_c}
    r = close.pct_change(fill_method=None)
    rs = pd.DataFrame({t: pd.concat([etf_c[e].pct_change(fill_method=None)[hedge[t] == e] for e in hedge[t].unique()])
                       .reindex(days) for t in tickers})

    # rolling beta over 60 days ending t-1 (matrix ops)
    m_rs, m_r = rs.rolling(BETA_WIN).mean(), r.rolling(BETA_WIN).mean()
    cov = (r * rs).rolling(BETA_WIN).mean() - m_r * m_rs
    var = (rs * rs).rolling(BETA_WIN).mean() - m_rs ** 2
    beta = (cov / var).shift(1)
    resid = r - beta * rs
    rvol = resid.rolling(BETA_WIN).std()
    sig = -resid.rolling(LOOK).sum() / rvol

    # daily books formed at close t
    in_uni = pd.DataFrame(False, index=days, columns=tickers)
    for y, names in members.items():
        rows = days.year == y
        cols = [t for t in tickers if t in names]
        in_uni.loc[rows, cols] = True
    s = sig.where(in_uni & close.notna() & beta.notna())
    z = s.sub(s.mean(axis=1), axis=0).div(s.std(axis=1), axis=0)
    stock_w = pd.DataFrame(0.0, index=days, columns=tickers)
    cands = []
    rank_hi = z.rank(axis=1, ascending=False)
    rank_lo = z.rank(axis=1, ascending=True)
    for d in days:
        if z.loc[d].notna().sum() < 4 * N_SIDE:
            continue
        longs = list(rank_hi.columns[(rank_hi.loc[d] <= N_SIDE).to_numpy()])
        shorts = list(rank_lo.columns[(rank_lo.loc[d] <= N_SIDE).to_numpy()])
        if drop:
            day = d.date().isoformat()
            longs = [t for t in longs if (day, t) not in drop]
            shorts = [t for t in shorts if (day, t) not in drop]
        if longs:
            stock_w.loc[d, longs] = 0.5 / len(longs)
        if shorts:
            stock_w.loc[d, shorts] = -0.5 / len(shorts)
        cands += [{"date": d.date().isoformat(), "ticker": t, "side": "long", "z": round(float(z.loc[d, t]), 3)}
                  for t in longs]
        cands += [{"date": d.date().isoformat(), "ticker": t, "side": "short", "z": round(float(z.loc[d, t]), 3)}
                  for t in shorts]
    # sector hedge per formation day
    etf_names = sorted(etf_c)
    hedge_w = pd.DataFrame(0.0, index=days, columns=etf_names)
    hb = (stock_w * beta.fillna(0.0))
    for e in etf_names:
        hedge_w[e] = -(hb.where(hedge == e, 0.0)).sum(axis=1)
    W = pd.concat([stock_w, hedge_w], axis=1)

    # position during open d -> open d+1: average of the books formed at closes d-1 .. d-5
    pos = sum(W.shift(k) for k in range(1, HOLD + 1)) / HOLD
    op = pd.concat([opn, pd.DataFrame({e: etf_o[e] for e in etf_names})], axis=1)[W.columns]
    R = (op.shift(-1) / op - 1)  # open d -> open d+1
    unscaled = (pos * R.fillna(0.0)).sum(axis=1)
    vol = unscaled.rolling(VOL_WIN).std().shift(1) * math.sqrt(252)
    gross = pos.abs().sum(axis=1)
    scale = np.minimum(0.10 / vol, 3.0 / gross.replace(0, np.nan)).fillna(0.0)
    P = pos.mul(scale, axis=0)
    turnover = P.diff().abs().sum(axis=1)
    short_gross = -P.clip(upper=0).sum(axis=1)
    ret = (P * R.fillna(0.0)).sum(axis=1) - turnover * cost_bps / 1e4 - 0.005 * short_gross / 252
    ret = ret[scale > 0].iloc[:-1]  # last day has no next open
    return ret.rename("statarb"), turnover, P.abs().sum(axis=1), pd.DataFrame(cands)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--window", choices=tuple(WINDOWS), required=True)
    ap.add_argument("--end", default="2026-09-26")
    args = ap.parse_args()
    lo, hi = WINDOWS[args.window]
    out: dict = {"window": args.window}
    rets = {}
    for bps in (5, 10, 25):
        ret, turn, gross, cands = build(args.end, bps)
        w = ret.loc[lo:hi]
        rets[bps] = w
        out[f"{bps}bps"] = stats(w)
        if bps == 10:
            out["turnover_per_day"] = round(float(turn.loc[lo:hi].mean()), 3)
            out["avg_gross"] = round(float(gross.loc[lo:hi].mean()), 2)
            cands.to_csv(BACKEND / "results" / "statarb_candidates.csv", index=False)
    for bps in (5, 10, 25):
        s = out[f"{bps}bps"]
        print(f"{args.window} @ {bps:2d} bps: CAGR {s['cagr_pct']}%  vol {s['vol_pct']}%  Sharpe {s['sharpe']}  "
              f"maxDD {s['max_drawdown_pct']}%  worst month {s['worst_month_pct']}%")
    print(f"  turnover {out['turnover_per_day']}x/day  avg gross {out['avg_gross']}x  yearly@10: "
          f"{out['10bps']['yearly_pct']}")

    if args.window == "holdout":
        w = rets[10]
        ci = np.percentile(block_boot([w.to_numpy()], sharpe), [2.5, 97.5])
        reg = [x for x in load_registry() if "sharpe_ann" in x]
        sh = [x["sharpe_ann"] / math.sqrt(252) for x in reg] + [w.mean() / w.std()]
        n_trials = len(load_registry()) + 1
        dsr = deflated_sharpe(w.to_numpy(), n_trials, float(np.var(sh, ddof=1)))
        b0 = master_b0("2018-01-02", args.end)
        both = pd.concat([b0, w], axis=1, join="inner").dropna()
        b1 = both["B0"] + both["statarb"]
        scaled = b1 * (both["B0"].std() / b1.std())
        dd = block_boot([both["B0"].to_numpy(), b1.to_numpy()], lambda a, b: sharpe(b) - sharpe(a))
        dlo, dhi = np.percentile(dd, [5, 95])
        s0, s1v = stats(both["B0"]), stats(scaled)
        rules = {"ci_above_0_at_10bps": bool(ci[0] > 0), "positive_at_25bps": out["25bps"]["sharpe"] > 0,
                 "dsr_above_0.95": dsr > 0.95,
                 "adds_value": bool(s1v["cagr_pct"] > s0["cagr_pct"] and dlo > 0)}
        out.update({"sharpe_ci95_10bps": [round(float(x), 2) for x in ci], "dsr": round(dsr, 3), "n_trials": n_trials,
                    "book": {"B0": s0, "B1_vol_matched": s1v,
                             "corr": round(float(both["B0"].corr(both["statarb"])), 2),
                             "sharpe_diff_ci90": [round(float(dlo), 2), round(float(dhi), 2)]},
                    "rules": rules, "pass": all(rules.values())})
        register({"trial": "statarb_arm_a", "date": time.strftime("%Y-%m-%d"), "kind": "sleeve",
                  "sharpe_ann": out["10bps"]["sharpe"], "window": "2018-2026", "result": "pass" if out["pass"] else "fail"})
        print(f"  Sharpe 95% CI @10bps {out['sharpe_ci95_10bps']}  DSR {out['dsr']} (N={n_trials})")
        print(f"  B0 CAGR {s0['cagr_pct']}%  B1 vol-matched {s1v['cagr_pct']}%  corr {out['book']['corr']}  "
              f"Sharpe diff 90% CI {out['book']['sharpe_diff_ci90']}")
        print(f"  rules {rules} -> {'PASS' if out['pass'] else 'FAIL'}")
    (BACKEND / "results" / f"statarb_{args.window}.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
