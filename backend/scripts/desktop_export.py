"""Chart data for the airp desktop app: writes results/desktop/metrics.json (read-only research; nothing trades).

    python scripts/desktop_export.py        # ~30 s; also run daily by autorun's post-run hook after the 18:00 check

Everything is recomputed from files already on disk with the frozen code, so the app never sees a number that the
research record doesn't have:
- tracks: the backtested core book and the event (AI) book from results/planner/track_returns.parquet, plus SPY,
  as weekly equity indexes (100 at the start), drawdowns, rolling 1-year Sharpe, yearly returns and summary stats;
- lab: every pre-registered strategy test in results/daytrade_test.json (Sharpe, 95% CI, window, verdict), and the
  net equity curves of the calendar and day-trading tests that can be rebuilt cheaply (D9, T1, O1, E1);
- live: the forward allocator's equity per book, the AI-picks sleeve history, and the live AI decision scores;
- per track, a full tear sheet (the panels other quant dashboards show: QuantStats, pyfolio and the like): risk
  ratios, tail risk, benchmark statistics, a month-by-month table, the worst drawdowns, rolling volatility and beta,
  the return distribution and a block-bootstrap cone for the next year;
- portfolio: the core book's allocation through time and today's risk picture (correlations, risk contributions,
  diversification ratio, effective number of independent bets);
- ai: how the judge's scores lined up with what happened next on the two backtest samples (score buckets, monthly
  rank correlation, sectors). Descriptive views of decisions already on file: no trial is run or registered here.
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np
import pandas as pd

OUT = BACKEND / "results" / "desktop" / "metrics.json"
FWD = BACKEND / "results" / "forward"


def _r(x: float, d: int = 4) -> float | None:
    return None if x is None or not math.isfinite(float(x)) else round(float(x), d)


def curve(ret: pd.Series, per_year: int = 252) -> dict[str, Any]:
    """Weekly-sampled equity index, drawdown and rolling 1-year Sharpe, plus summary stats, from daily returns."""
    ret = ret.dropna().sort_index()
    eq = (1 + ret).cumprod() * 100
    dd = eq / eq.cummax() - 1
    roll = ret.rolling(per_year).mean() / ret.rolling(per_year).std() * np.sqrt(per_year)
    wk = pd.DataFrame({"eq": eq, "dd": dd, "rs": roll}).resample("W-FRI").last().dropna(subset=["eq"])
    years = len(ret) / per_year
    yearly = (1 + ret).groupby(ret.index.year).prod() - 1
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in wk.index],
        "equity": [_r(v, 2) for v in wk["eq"]], "drawdown": [_r(v) for v in wk["dd"]],
        "rolling_sharpe": [_r(v, 2) for v in wk["rs"]],
        "stats": {"cagr": _r((eq.iloc[-1] / 100) ** (1 / years) - 1),
                  "vol": _r(ret.std() * np.sqrt(per_year)), "sharpe": _r(ret.mean() / ret.std() * np.sqrt(per_year), 2),
                  "max_dd": _r(dd.min()), "start": ret.index[0].strftime("%Y-%m-%d"),
                  "end": ret.index[-1].strftime("%Y-%m-%d")},
        "yearly": {str(y): _r(v) for y, v in yearly.items()},
    }


def psr(ret: pd.Series) -> float:
    """Probabilistic Sharpe ratio: the chance the true Sharpe is above 0 given the sample's length, skew and tails
    (Bailey & Lopez de Prado 2012)."""
    from statistics import NormalDist
    sr = ret.mean() / ret.std()
    den = math.sqrt(max(1e-12, 1 - ret.skew() * sr + (ret.kurt() + 2) / 4 * sr * sr))
    return NormalDist().cdf(sr * math.sqrt(len(ret) - 1) / den)


def drawdown_periods(ret: pd.Series, n: int = 5) -> list[dict[str, Any]]:
    """The n deepest peak-to-recovery episodes: start (last high), trough, recovery date (None if still under)."""
    eq = (1 + ret).cumprod()
    dd = eq / eq.cummax() - 1
    under = dd < 0
    grp = (under != under.shift()).cumsum()[under]
    out = []
    for _, g in dd[under].groupby(grp):
        start = dd.index[max(0, dd.index.get_loc(g.index[0]) - 1)]
        after = dd.index.get_loc(g.index[-1]) + 1
        rec = dd.index[after] if after < len(dd) else None
        out.append({"start": start.strftime("%Y-%m-%d"), "trough": g.idxmin().strftime("%Y-%m-%d"),
                    "recovered": rec.strftime("%Y-%m-%d") if rec is not None else None, "depth": _r(g.min()),
                    "days": int(((rec if rec is not None else dd.index[-1]) - start).days)})
    return sorted(out, key=lambda d: d["depth"])[:n]


def bootstrap_cone(ret: pd.Series, horizon: int = 252, paths: int = 2000, block: int = 21,
                   seed: int = 0) -> dict[str, Any]:
    """Where 100 could be a year from now if the future resembled the past: whole 21-day blocks of real daily returns
    are redrawn (so bad stretches stay together) 2,000 times. A what-if from history, not a forecast."""
    x = ret.to_numpy()
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, len(x) - block, size=(paths, horizon // block))
    sample = x[(starts[:, :, None] + np.arange(block)).reshape(paths, -1)]
    eq = 100 * np.cumprod(1 + sample, axis=1)
    month = np.arange(block, sample.shape[1] + 1, block) - 1
    pcts = {f"p{q}": [100.0] + [_r(v, 1) for v in np.percentile(eq[:, month], q, axis=0)] for q in (5, 25, 50, 75, 95)}
    dd = (eq / np.maximum.accumulate(eq, axis=1) - 1).min(axis=1)
    return {"months": list(range(len(month) + 1)), **pcts, "prob_loss": _r((eq[:, -1] < 100).mean(), 3),
            "prob_dd20": _r((dd <= -0.20).mean(), 3), "median_dd": _r(float(np.median(dd)), 3), "paths": paths}


def tearsheet(ret: pd.Series, bench: pd.Series | None = None, per_year: int = 252) -> dict[str, Any]:
    ret = ret.dropna().sort_index()
    eq = (1 + ret).cumprod()
    dd = eq / eq.cummax() - 1
    years = len(ret) / per_year
    cagr = eq.iloc[-1] ** (1 / years) - 1
    mon = (1 + ret).groupby([ret.index.year, ret.index.month]).prod() - 1
    downside = math.sqrt(float((np.minimum(ret, 0) ** 2).mean()))
    var95 = float(ret.quantile(0.05))
    under = (dd < 0).astype(int)
    longest = int(under.groupby((under == 0).cumsum()).sum().max())
    stats = {"sortino": _r(ret.mean() / downside * math.sqrt(per_year), 2), "calmar": _r(cagr / abs(dd.min()), 2),
             "var95": _r(var95), "cvar95": _r(ret[ret <= var95].mean()), "ulcer": _r(math.sqrt(float((dd ** 2).mean()))),
             "tail_ratio": _r(abs(ret.quantile(0.95) / var95), 2), "skew": _r(ret.skew(), 2), "kurtosis": _r(ret.kurt(), 2),
             "best_day": _r(ret.max()), "worst_day": _r(ret.min()), "best_month": _r(mon.max()),
             "worst_month": _r(mon.min()), "pos_days": _r((ret > 0).mean(), 3), "pos_months": _r((mon > 0).mean(), 3),
             "avg_up_month": _r(mon[mon > 0].mean()), "avg_down_month": _r(mon[mon < 0].mean()),
             "longest_underwater_days": longest, "time_underwater": _r((dd < 0).mean(), 3), "psr": _r(psr(ret), 3)}
    table = mon.unstack()
    out: dict[str, Any] = {
        "stats": stats,
        "monthly": {"years": [str(y) for y in table.index],
                    "cells": [[_r(v) if pd.notna(v) else None for v in row] for row in table.reindex(columns=range(1, 13)).to_numpy()]},
        "drawdowns": drawdown_periods(ret),
        "cone": bootstrap_cone(ret),
    }
    lo, hi = float(mon.min()), float(mon.max())
    edges = np.linspace(math.floor(lo * 50) / 50, math.ceil(hi * 50) / 50, 21)
    out["month_hist"] = {"edges": [_r(e, 3) for e in edges], "counts": np.histogram(mon, bins=edges)[0].tolist()}
    roll_vol = ret.rolling(126).std() * math.sqrt(per_year)
    roll = {"vol": roll_vol}
    if bench is not None:
        b = bench.reindex(ret.index).fillna(0.0)
        beta = float(ret.cov(b) / b.var())
        bm = ((1 + b).groupby([b.index.year, b.index.month]).prod() - 1).reindex(mon.index)
        stats.update({"beta": _r(beta, 2), "alpha": _r((ret.mean() - beta * b.mean()) * per_year), "corr": _r(ret.corr(b), 2),
                      "up_capture": _r(mon[bm > 0].mean() / bm[bm > 0].mean(), 2),
                      "down_capture": _r(mon[bm < 0].mean() / bm[bm < 0].mean(), 2),
                      "info_ratio": _r((ret - b).mean() / (ret - b).std() * math.sqrt(per_year), 2)})
        roll["beta"] = ret.rolling(126).cov(b) / b.rolling(126).var()
    wk = pd.DataFrame(roll).resample("W-FRI").last().dropna(how="all")
    out["rolling"] = {"dates": [d.strftime("%Y-%m-%d") for d in wk.index],
                      **{k: [_r(v, 3) if pd.notna(v) else None for v in wk[k]] for k in wk.columns}}
    return out


def tracks() -> dict[str, Any]:
    t = pd.read_parquet(BACKEND / "results" / "planner" / "track_returns.parquet")
    t.index = pd.DatetimeIndex(t.index)
    spy = pd.read_parquet(BACKEND / "data" / "trend" / "etf_closes.parquet")["SPY"].pct_change()
    spy = spy.loc[t.index[0]:t.index[-1]]
    out = {"core": curve(t["core"]), "event": curve(t["event"]), "SPY": curve(spy)}
    out["core"]["tear"] = tearsheet(t["core"], spy)
    out["event"]["tear"] = tearsheet(t["event"], spy)
    out["SPY"]["tear"] = tearsheet(spy)
    return out


def portfolio() -> dict[str, Any]:
    """The core book's allocation through time (targets x the drawdown-brake multiplier, the rest in cash/T-bills)
    and today's risk picture from the last year of daily returns."""
    from datetime import date

    from drawdown_brakes import brake
    from master_portfolio import load_crypto

    from app.portfolio.master import MasterConfig, allocate, crypto_state, simulate_weights
    from app.sandbox.events import Prices
    px = Prices.from_long(load_crypto("2099-12-31"))
    cfg = MasterConfig()
    days = [d for d in px.close.index if d.date() >= date(2018, 1, 2)]
    targets = {d.date(): allocate([], crypto_state(px.close, d, cfg.crypto_assets), cfg).weights for d in days[::5]}
    sim = simulate_weights(targets, px.open, px.close, days[0].date(), days[-1].date())
    eq = pd.Series(sim.equity, index=pd.to_datetime(sim.days))
    _, mult = brake(eq.pct_change().dropna())
    assets = ["SPY", "BTC-USD", "ETH-USD"]
    w = pd.DataFrame({pd.Timestamp(x["day"]): x["targets"] for x in sim.weights_log}).T.reindex(columns=assets).fillna(0.0)
    w = w.reindex(mult.index, method="ffill").fillna(0.0).mul(mult, axis=0)
    w["Cash"] = (1 - w.sum(axis=1)).clip(lower=0)
    wk = w.resample("W-FRI").last().dropna()
    rets = px.close[assets].pct_change().dropna().iloc[-252:]
    cov = rets.cov() * 252
    now = w[assets].iloc[-1]
    vol_p = math.sqrt(float(now @ cov @ now))
    rc = now * (cov @ now) / (vol_p ** 2) if vol_p > 0 else now * 0
    vols = np.sqrt(np.diag(cov))
    return {"dates": [d.strftime("%Y-%m-%d") for d in wk.index],
            "weights": {c: [_r(v, 3) for v in wk[c]] for c in wk.columns},
            "now": {c: _r(v, 3) for c, v in w.iloc[-1].items()},
            "brake": _r(mult.iloc[-1], 2), "brake_days": _r((mult < 1).mean(), 3),
            "trades": sim.trades, "cost_paid_pct": _r(sim.costs / 100_000, 4),
            "assets": assets, "corr": [[_r(v, 2) for v in row] for row in rets.corr().to_numpy()],
            "asset_vol": {a: _r(v, 3) for a, v in zip(assets, vols, strict=True)},
            "risk_contrib": {a: _r(v, 3) for a, v in rc.items()}, "port_vol": _r(vol_p, 3),
            "div_ratio": _r(float(now @ vols) / vol_p, 2) if vol_p > 0 else None,
            "eff_bets": _r(1 / float((rc ** 2).sum()), 2) if vol_p > 0 else None}


def ai() -> dict[str, Any]:
    """How the judge's 1-week scores lined up with the stock's next 5 days against its sector, on the two backtest
    samples. Descriptive only (the decisions are already on file; nothing is re-run or registered)."""
    from bonsai_lite import SAMPLES
    from event_eval import build
    from secchk_eval import load

    from app.sandbox.events import Prices
    p = Prices.from_long(pd.read_parquet(BACKEND / "data/events/ohlcv_2023-01-01_2026-09-25.parquet"))
    frames = []
    for name, (tag, events, _) in SAMPLES.items():
        df = build(pd.read_csv(BACKEND / events), p)
        frames.append(df[df["scorable"]].merge(load(tag, 5), on="accession").assign(sample=name))
    d = pd.concat(frames, ignore_index=True).dropna(subset=["fwd5", "logodds"])
    book = FWD / "ai_picks" / "book.json"
    thr = json.loads(book.read_text()).get("threshold") if book.exists() else None

    def grp(g: pd.DataFrame) -> dict[str, Any]:
        return {"n": len(g), "score": _r(g["logodds"].mean(), 2), "excess": _r(g["fwd5"].mean()),
                "hit": _r((g["fwd5"] > 0).mean(), 3)}
    d["bucket"] = pd.qcut(d["logodds"], 10, labels=False, duplicates="drop")
    monthly = [{"month": m, "ic": _r(g["logodds"].corr(g["fwd5"], method="spearman"), 3), "n": len(g)}
               for m, g in d.groupby("month") if len(g) >= 20]
    pts = d.sample(min(len(d), 600), random_state=0)
    return {"n": len(d), "threshold": thr, "samples": {k: grp(g) for k, g in d.groupby("sample")},
            "ic": _r(d["logodds"].corr(d["fwd5"], method="spearman"), 3),
            "buckets": [grp(g) for _, g in d.groupby("bucket")],
            "above": grp(d[d["logodds"] >= thr]) if thr is not None else None,
            "below": grp(d[d["logodds"] < thr]) if thr is not None else None,
            "monthly_ic": monthly,
            "sectors": sorted(({"sector": k, **grp(g)} for k, g in d.groupby("sector")), key=lambda r: -r["n"]),
            "scatter": [[_r(a, 2), _r(b)] for a, b in zip(pts["logodds"], pts["fwd5"].clip(-0.3, 0.3), strict=True)]}


def _stats_of(v: dict[str, Any]) -> dict[str, Any]:
    for key in ("1bp", "excess", "10bp", "5bp"):
        s = v.get(key)
        if isinstance(s, dict):
            s = s.get("both", s)
            if "sharpe" in s:
                return s
    for s in v.values():
        if isinstance(s, dict) and "sharpe" in s:
            return s
    return {}


GROUP = {"D": "day trading", "P": "pairs", "C": "crypto carry", "T": "calendar", "O": "calendar", "E": "calendar"}
NAMES = {"D1": "Intraday momentum", "D2": "Opening-range breakout", "D3": "Noise-band VWAP", "D4": "Rest-of-day momentum",
         "D5": "End-of-day reversal", "D6": "Box theory", "D7": "Intraday periodicity", "D8": "Darvas box",
         "D9": "Opening-auction reversal", "D10": "Pre-market reversal", "P1": "Pairs (GGR)", "C1": "Funding carry",
         "T1": "Turn of the month", "O1": "SPY overnight", "E1": "Macro announcements"}


def both(x: Any) -> dict[str, Any]:
    """A result block; intraday tests nest theirs under "both" (SPY and QQQ together)."""
    return x.get("both", x) if isinstance(x, dict) else {}


def lab() -> dict[str, Any]:
    d = json.loads((BACKEND / "results" / "daytrade_test.json").read_text())
    tests = []
    for k, v in d.items():
        s = _stats_of(v)
        costs = {c: both(v[c]).get("sharpe") for c in sorted((c for c in v if c.endswith("bp") and c[:-2].isdigit()),
                                                             key=lambda c: int(c[:-2]))}
        before = both(v.get("before_window") or v.get("before_window_excess") or {})
        tests.append({"id": k, "name": NAMES.get(k, k), "group": GROUP.get(k[0], "other"), "window": v.get("window"),
                      "sharpe": s.get("sharpe"), "ci": s.get("ci"), "cagr": s.get("cagr"), "pass": bool(v.get("pass")),
                      "days": s.get("days"), "hit_rate": s.get("hit_rate"), "worst_month": s.get("worst_month"),
                      "costs": costs, "before_sharpe": before.get("sharpe"), "corr_core": v.get("corr_with_core")})
    curves: dict[str, Any] = {}
    try:  # rebuild the cheap net curves with the frozen functions
        from vol_target_b0 import tbill

        from app.sandbox.calendar_fx import tom_overlay
        from app.sandbox.overnight import overnight_excess, overnight_legs
        spy = pd.read_parquet(BACKEND / "data" / "trend" / "etf_closes.parquet")["SPY"].dropna()
        rf = tbill().reindex(spy.index, method="ffill").fillna(0.0) / 252
        ex = (spy.pct_change() - rf).dropna()
        curves["T1"] = curve(tom_overlay(ex, 1e-4)["ret"].loc["2008-01-02":])
        etf = pd.read_parquet(BACKEND / "data" / "statarb" / "sector_etfs.parquet")
        legs = overnight_legs(etf[("SPY", "Open")], etf[("SPY", "Close")])
        curves["O1"] = curve(overnight_excess(legs, rf.reindex(legs.index, method="ffill").fillna(0.0), 1e-4).loc["2012-01-03":])
        cal = BACKEND / "data" / "macro" / "announcements.csv"
        if cal.exists():
            from app.sandbox.macro_events import announcement_strategy, event_days
            x = ex.loc["2013-05-01":]
            ev = event_days(pd.DatetimeIndex(x.index), pd.read_csv(cal, dtype=str)["date"].tolist())
            curves["E1"] = curve(announcement_strategy(x, ev, 1e-4)["ret"])
    except Exception as e:  # noqa: BLE001 - a missing input drops a curve, never the export
        curves["error_calendar"] = f"{type(e).__name__}: {e}"[:200]
    try:
        from daytrade_test import daily_list

        from app.sandbox.intraday import d9_open_reversal
        daily, names = daily_list()
        curves["D9"] = curve(d9_open_reversal(daily, names, 1e-4)["ret"].loc["2016-01-04":])
    except Exception as e:  # noqa: BLE001
        curves["error_d9"] = f"{type(e).__name__}: {e}"[:200]
    return {"tests": tests, "curves": curves}


def _jsonl(p: Path) -> list[dict[str, Any]]:
    out = []
    if p.exists():
        for line in p.read_text(errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def live() -> dict[str, Any]:
    runs = _jsonl(FWD / "allocator" / "ledger.jsonl")
    books: dict[str, list[list[Any]]] = {}
    for r in runs:
        for name, b in (r.get("books") or {}).items():
            books.setdefault(name, []).append([r.get("data_through"), b.get("equity")])
    book = FWD / "ai_picks" / "book.json"
    ai = json.loads(book.read_text()) if book.exists() else {}
    dec = [r for r in _jsonl(FWD / "events" / "ledger.jsonl") if r.get("type") == "decision"]
    return {"books": books, "ai_history": ai.get("history", []), "ai_threshold": ai.get("threshold"),
            "decisions": [{"date": str(r.get("as_of", ""))[:10], "ticker": r.get("ticker"), "logodds": r.get("logodds")}
                          for r in dec]}


def main() -> None:
    t0 = time.monotonic()
    out = {"generated": datetime.now(UTC).isoformat(timespec="seconds"), "tracks": tracks(), "lab": lab(), "live": live()}
    for key, fn in (("portfolio", portfolio), ("ai", ai)):  # a missing input drops a panel, never the export
        try:
            out[key] = fn()
        except Exception as e:  # noqa: BLE001
            out[key] = {"error": f"{type(e).__name__}: {e}"[:200]}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, separators=(",", ":")))
    tmp.replace(OUT)
    print(f"wrote {OUT.relative_to(BACKEND)} ({OUT.stat().st_size // 1024} KB) in {time.monotonic() - t0:.0f}s; "
          f"lab curves: {sorted(k for k in out['lab']['curves'] if not k.startswith('error'))}")


if __name__ == "__main__":
    main()
