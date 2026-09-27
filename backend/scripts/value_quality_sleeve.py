"""PLAN_60 Phase 2 item 3: value + quality long-short sleeve on the point-in-time top-100 universe.

    python scripts/value_quality_sleeve.py

Spec (docs/PLAN_60.md, fixed before the run). Point-in-time fundamentals from SEC XBRL companyfacts (usable the day
after `filed`): value = book equity / market cap (market cap = actual month-end price x latest filed dei shares
outstanding; actual price = Yahoo's split-adjusted Close x the splits after that day, because filed share counts are
not split-adjusted); quality = TTM gross profit / total assets (GrossProfit, else revenue - cost of revenue; TTM =
last annual + current YTD - prior-year YTD, else last annual). Score = mean of the two cross-sectional z-scores (both
required). Monthly: long top 20% / short bottom 20% of that year's universe, weights set at close t earn from t+1,
10 bps x turnover, 0.5%/yr borrow on shorts, scaled to 10% vol (126-day), cap 2x. Standalone window 2014-01 on;
adding rule vs B0 2018-26; DSR on the registry's N.
"""
from __future__ import annotations

import gzip
import json
import math
import sys
import time
import urllib.request
from datetime import timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np
import pandas as pd
from statarb_b_stage1 import user_agent
from trend_sleeve import block_boot, master_b0, sharpe, stats

from app.sandbox.dsr import deflated_sharpe, load_registry, register

HIST = BACKEND / "data" / "hist"
VQ = BACKEND / "data" / "vq"
REV = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
       "RevenueFromContractWithCustomerIncludingAssessedTax"]
COST = ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization"]


def facts(cik: int, ua: str) -> dict | None:
    f = VQ / "facts" / f"{cik}.json.gz"
    if f.exists():
        return json.loads(gzip.decompress(f.read_bytes()))
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "identity"})
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = json.loads(r.read().decode())
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2 * (attempt + 1))
        except Exception:
            time.sleep(2 * (attempt + 1))
    else:
        return None
    keep = {"us-gaap": {}, "dei": {}}
    for ns, names in (("us-gaap", ["StockholdersEquity", "Assets", "GrossProfit", *REV, *COST]),
                      ("dei", ["EntityCommonStockSharesOutstanding"])):
        for n in names:
            if n in raw.get("facts", {}).get(ns, {}):
                keep[ns][n] = raw["facts"][ns][n]["units"]
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(gzip.compress(json.dumps(keep).encode()))
    time.sleep(0.15)
    return keep


def series(fx: dict, ns: str, name: str, unit: str) -> pd.DataFrame:
    rows = fx.get(ns, {}).get(name, {}).get(unit, [])
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["filed"] = pd.to_datetime(df["filed"])
    df["end"] = pd.to_datetime(df["end"])
    if "start" in df:
        df["start"] = pd.to_datetime(df["start"])
    return df.dropna(subset=["val"])


def instant_asof(df: pd.DataFrame, t: pd.Timestamp) -> float | None:
    if df.empty:
        return None
    d = df[df["filed"] < t]
    if d.empty:
        return None
    d = d[d["end"] == d["end"].max()]
    return float(d.sort_values("filed")["val"].iloc[-1])


def flow_ttm_asof(df: pd.DataFrame, t: pd.Timestamp) -> float | None:
    """TTM value of a duration fact known before t: last annual + current YTD - prior-year YTD, else last annual."""
    if df.empty or "start" not in df:
        return None
    d = df[(df["filed"] < t)].copy()
    if d.empty:
        return None
    d["days"] = (d["end"] - d["start"]).dt.days
    annual = d[(d["days"] >= 350) & (d["days"] <= 380)]
    if annual.empty:
        return None
    fy = annual[annual["end"] == annual["end"].max()].sort_values("filed").iloc[-1]
    ytd = d[(d["days"] >= 80) & (d["days"] < 350) & (d["end"] > fy["end"])]
    if ytd.empty:
        return float(fy["val"])
    cur = ytd[ytd["end"] == ytd["end"].max()].sort_values("filed").iloc[-1]
    prior = d[(abs((d["end"] - (cur["end"] - timedelta(days=365))).dt.days) <= 10)
              & (abs(d["days"] - cur["days"]) <= 10)]
    if prior.empty:
        return float(fy["val"])
    return float(fy["val"] + cur["val"] - prior.sort_values("filed")["val"].iloc[-1])


def gross_profit_df(fx: dict) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    gp = series(fx, "us-gaap", "GrossProfit", "USD")
    if not gp.empty:
        return gp, None
    rev = next((s for s in (series(fx, "us-gaap", n, "USD") for n in REV) if not s.empty), pd.DataFrame())
    cost = next((s for s in (series(fx, "us-gaap", n, "USD") for n in COST) if not s.empty), pd.DataFrame())
    return (rev, cost) if not rev.empty and not cost.empty else (pd.DataFrame(), None)


def prices_and_splits(tickers: list[str]) -> tuple[pd.DataFrame, dict[str, pd.Series]]:
    pf, sf = VQ / "close_split_adj.parquet", VQ / "splits.json"
    import yfinance as yf
    if not pf.exists():
        df = yf.download(tickers, start="2009-01-01", end="2026-09-26", auto_adjust=False, progress=False)["Close"]
        missing = [t for t in tickers if t not in df or df[t].isna().all()]
        if missing:
            time.sleep(5)
            again = yf.download(missing, start="2009-01-01", end="2026-09-26", auto_adjust=False, progress=False)["Close"]
            if isinstance(again, pd.Series):
                again = again.to_frame(missing[0])
            df = df.drop(columns=[c for c in missing if c in df]).join(again, how="outer")
        VQ.mkdir(parents=True, exist_ok=True)
        df.to_parquet(pf)
    if not sf.exists():
        out = {}
        for t in tickers:
            try:
                s = yf.Ticker(t).splits
                out[t] = {str(k.date()): float(v) for k, v in s.items()}
            except Exception:
                out[t] = {}
            time.sleep(0.2)
        sf.write_text(json.dumps(out))
    close = pd.read_parquet(pf)
    close.index = pd.to_datetime(close.index).tz_localize(None)
    splits = {t: pd.Series(v, index=pd.to_datetime(list(v))) if v else pd.Series(dtype=float)
              for t, v in json.loads(sf.read_text()).items()}
    return close, splits


def main() -> None:
    uni = pd.read_csv(HIST / "universe_2010_2026_top100.csv")
    uni["ticker"] = uni["ticker"].str.replace(".", "-", regex=False)
    members = {int(y): set(g["ticker"]) for y, g in uni.groupby("year")}
    tickers = sorted(set(uni["ticker"]))
    mem = pd.read_csv(BACKEND / "data/xbrl/members_2010_2026.csv")
    mem["ticker"] = mem["ticker"].str.replace(".", "-", regex=False)
    cik = mem.dropna(subset=["cik"]).groupby("ticker")["cik"].last().astype(int).to_dict()
    ua = user_agent()
    fx = {}
    for t in tickers:
        if t in cik:
            fx[t] = facts(cik[t], ua)
    print(f"{len(tickers)} tickers, {sum(v is not None for v in fx.values())} with XBRL facts", flush=True)

    close, splits = prices_and_splits(tickers)
    adj = pd.read_parquet(HIST / "ohlcv_2010_2026_top100.parquet")
    adj["Date"] = pd.to_datetime(adj["Date"])
    tr = adj.pivot(index="Date", columns="Ticker", values="Close").sort_index()  # total-return closes for P&L
    days = tr.index
    month_ends = days.to_series().groupby(days.to_period("M")).last()
    ser = {t: {"eq": series(f, "us-gaap", "StockholdersEquity", "USD"), "assets": series(f, "us-gaap", "Assets", "USD"),
               "shares": series(f, "dei", "EntityCommonStockSharesOutstanding", "shares"), "gp": gross_profit_df(f)}
           for t, f in fx.items() if f}
    scores = {}
    for t_me in month_ends:
        if t_me.year not in members or t_me < pd.Timestamp("2011-01-01"):
            continue
        rows = {}
        for tk in members[t_me.year]:
            if tk not in ser or tk not in close.columns:
                continue
            s = ser[tk]
            px = close[tk].loc[:t_me].dropna()
            if px.empty:
                continue
            factor = float(splits.get(tk, pd.Series(dtype=float)).loc[lambda x: x.index > t_me].prod()) \
                if len(splits.get(tk, [])) else 1.0
            actual = float(px.iloc[-1]) * (factor if factor > 0 else 1.0)
            eq, sh, assets = instant_asof(s["eq"], t_me), instant_asof(s["shares"], t_me), instant_asof(s["assets"], t_me)
            value = eq / (actual * sh) if eq and sh and eq > 0 and actual > 0 else None
            gp_df, cost_df = s["gp"]
            if cost_df is None:
                gp = flow_ttm_asof(gp_df, t_me)
            else:
                rv, cs = flow_ttm_asof(gp_df, t_me), flow_ttm_asof(cost_df, t_me)
                gp = rv - cs if rv is not None and cs is not None else None
            quality = gp / assets if gp is not None and assets else None
            rows[tk] = (value, quality)
        df = pd.DataFrame(rows, index=["value", "quality"]).T.astype(float).dropna()
        if len(df) < 30:
            continue
        z = (df - df.mean()) / df.std()
        z["value"] = z["value"].clip(-3, 3)
        z["quality"] = z["quality"].clip(-3, 3)
        scores[t_me] = z.mean(axis=1)
    print(f"{len(scores)} monthly cross-sections, median names {int(np.median([len(v) for v in scores.values()]))}",
          flush=True)

    W = {}
    for t_me, sc in scores.items():
        q = sc.rank(pct=True)
        w = pd.Series(0.0, index=tr.columns)
        longs, shorts = q[q > 0.8].index, q[q <= 0.2].index
        w[longs], w[shorts] = 1.0 / len(longs), -1.0 / len(shorts)
        W[t_me] = w
    W = pd.DataFrame(W).T.sort_index()
    r = tr.pct_change(fill_method=None)
    raw = W.reindex(days).ffill().shift(1).dropna(how="all")
    unscaled = (raw * r.loc[raw.index].fillna(0.0)).sum(axis=1)
    scale = {}
    for t_me in W.index:
        h = unscaled.loc[:t_me].iloc[-126:]
        scale[t_me] = min(2.0, 0.10 / (h.std() * math.sqrt(252))) if len(h) >= 126 and h.std() > 0 else np.nan
    scale = pd.Series(scale).dropna()
    Ws = W.loc[scale.index].mul(scale, axis=0)
    dw = Ws.reindex(days).ffill().shift(1).dropna(how="all")
    turnover = Ws.diff().abs().sum(axis=1)
    cost = pd.Series(0.0, index=dw.index)
    for t_me, tv in turnover.fillna(Ws.iloc[0].abs().sum()).items():
        nxt = dw.index[dw.index > t_me]
        if len(nxt):
            cost[nxt[0]] += tv * 10 / 1e4
    ret = ((dw * r.loc[dw.index].fillna(0.0)).sum(axis=1) - cost - 0.005 * (-dw.clip(upper=0).sum(axis=1)) / 252
           ).rename("vq")
    post = ret.loc["2014-01-01":]
    ci = np.percentile(block_boot([post.to_numpy()], sharpe), [2.5, 97.5])
    reg = [x for x in load_registry() if "sharpe_ann" in x]
    sh = [x["sharpe_ann"] / math.sqrt(252) for x in reg] + [post.mean() / post.std()]
    n = len(load_registry()) + 1
    dsr = deflated_sharpe(post.to_numpy(), n, float(np.var(sh, ddof=1)))
    b0 = master_b0("2018-01-02", "2026-09-26")
    both = pd.concat([b0, ret], axis=1, join="inner").dropna()
    b1 = both["B0"] + both["vq"]
    scaled = b1 * (both["B0"].std() / b1.std())
    d = block_boot([both["B0"].to_numpy(), b1.to_numpy()], lambda a, b: sharpe(b) - sharpe(a))
    lo, hi = np.percentile(d, [5, 95])
    s0, s1v = stats(both["B0"]), stats(scaled)
    rules = {"standalone_ci_above_0": bool(ci[0] > 0), "adds_value": bool(s1v["cagr_pct"] > s0["cagr_pct"] and lo > 0),
             "dsr_above_0.95": dsr > 0.95}
    out = {"full": stats(ret), "post_2014": stats(post), "sharpe_ci95": [round(float(x), 2) for x in ci],
           "dsr": round(dsr, 3), "n_trials": n, "turnover_per_year": round(float(turnover.mean() * 12), 2),
           "book": {"B0": s0, "B1_vol_matched": s1v, "corr": round(float(both["B0"].corr(both["vq"])), 2),
                    "sharpe_diff_ci90": [round(float(lo), 2), round(float(hi), 2)]},
           "rules": rules, "pass": all(rules.values())}
    (BACKEND / "results" / "value_quality_sleeve.json").write_text(json.dumps(out, indent=1))
    register({"trial": "value_quality_ls", "date": time.strftime("%Y-%m-%d"), "kind": "sleeve",
              "sharpe_ann": out["post_2014"]["sharpe"], "window": "2014-2026", "result": "pass" if out["pass"] else "fail"})
    p = out["post_2014"]
    print(f"2014-26: CAGR {p['cagr_pct']}%  vol {p['vol_pct']}%  Sharpe {p['sharpe']} CI {out['sharpe_ci95']}  maxDD "
          f"{p['max_drawdown_pct']}%  worst year {p['worst_year_pct']}%  turnover {out['turnover_per_year']}x/yr")
    print(f"yearly {out['full']['yearly_pct']}")
    print(f"B0 CAGR {s0['cagr_pct']}%  B1 vol-matched {s1v['cagr_pct']}%  corr {out['book']['corr']}  "
          f"Sharpe diff 90% CI {out['book']['sharpe_diff_ci90']}  DSR {out['dsr']} (N={n})")
    print(f"rules {rules} -> {'PASS' if out['pass'] else 'FAIL'}")


if __name__ == "__main__":
    main()
