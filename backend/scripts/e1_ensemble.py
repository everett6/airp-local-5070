"""E1 minute-bar signal ensemble, run once (docs/PLAN_60_V2.md, "E1"). Writes results/e1_ensemble.json,
the live model results/forward/algo/model.json, and the registry line.

    python scripts/e1_ensemble.py
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import numpy as np
import pandas as pd

from app.sandbox import minute_ensemble as me
from app.sandbox.dsr import register

TRIAL = "minute_signal_ensemble_e1"
DATA = BACKEND / "data" / "intraday"
TRAIN_FROM, OOS_FROM, OOS_TO = date(2021, 1, 4), date(2023, 1, 3), date(2026, 9, 25)
GROSS = 3.0


def load(symbol: str) -> pd.DataFrame:
    df = pd.read_parquet(DATA / f"{symbol}_1min.parquet")
    return df[df["ts"].dt.date >= date(2020, 11, 1)]


def panel() -> pd.DataFrame:
    lead = {s: load(s) for s in ("SPY", "QQQ")}
    parts = []
    for s in me.UNIVERSE:
        f = me.frame(lead["SPY"] if s == "SPY" else load(s), lead[me.leader_of(s)])
        f = f[f["m"].isin(me.DECISION_BARS) & (f["date"] >= TRAIN_FROM) & (f["date"] <= OOS_TO)]
        f["symbol"] = s
        parts.append(f)
    p = pd.concat(parts)
    ok = np.isfinite(p[["sigma", *me.FEATURES]]).all(axis=1) & (p["sigma"] > 0)
    return p[ok]


def backtest(p: pd.DataFrame) -> tuple[pd.Series, me.Ridge]:
    """Walk-forward predictions (refit each month on everything before it), then the book's daily net return."""
    x = p[list(me.FEATURES)].to_numpy()
    p = p.assign(pred=np.nan)
    months = pd.period_range(OOS_FROM, OOS_TO, freq="M")
    dates = pd.to_datetime(p["date"])
    model = None
    for mo in months:
        start = mo.start_time
        train = (dates < start) & np.isfinite(p["y"]).to_numpy()
        model = me.Ridge.fit(x[train.to_numpy()], p["y"].to_numpy()[train.to_numpy()])
        rows = ((dates >= start) & (dates <= mo.end_time)).to_numpy()
        p.loc[rows, "pred"] = model.predict(x[rows])
    oos = p[p["pred"].notna() & p["fwd"].notna()].copy()
    ret = oos["pred"] * oos["sigma"]
    c = oos["symbol"].map(me.cost).astype(float)
    oos["w"] = np.where(ret.abs() > 2 * c, np.sign(ret), 0.0) * GROSS / len(me.UNIVERSE)
    oos = oos.sort_values(["symbol", "date", "m"])
    prev = oos.groupby(["symbol", "date"])["w"].shift(1).fillna(0.0)
    last = oos.groupby(["symbol", "date"])["m"].transform("max") == oos["m"]
    turnover = (oos["w"] - prev).abs() + np.where(last, oos["w"].abs(), 0.0)
    oos["net"] = oos["w"] * oos["fwd"] - turnover * c
    oos["gross_ret"] = oos["w"] * oos["fwd"]
    daily = oos.groupby("date")[["net", "gross_ret"]].sum()
    daily.attrs["turnover_per_day"] = float(turnover.groupby(oos["date"]).sum().mean())
    daily.attrs["held_share"] = float((oos["w"] != 0).mean())
    # final model on all data, for the live engine
    full = np.isfinite(p["y"]).to_numpy()
    model = me.Ridge.fit(x[full], p["y"].to_numpy()[full])
    return daily, model


def sharpe(r: np.ndarray) -> float:
    return float(r.mean() / r.std(ddof=1) * np.sqrt(252)) if r.std(ddof=1) > 0 else 0.0


def main() -> None:
    p = panel()
    daily, model = backtest(p)
    r = daily["net"].to_numpy()
    rng = np.random.default_rng(7)
    boots = [sharpe(r[rng.integers(0, len(r), len(r))]) for _ in range(2000)]
    lo, hi = float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))
    years = {str(y): float(np.prod(1 + g["net"]) - 1) for y, g in daily.groupby(pd.to_datetime(daily.index).year)}
    s = sharpe(r)
    passed = s >= 1.0 and lo > 0 and all(v > 0 for v in years.values())
    eq = np.cumprod(1 + r)
    out = {"trial": TRIAL, "run_at": datetime.now(UTC).isoformat(), "oos": [str(OOS_FROM), str(OOS_TO)],
           "days": len(r), "gross": GROSS, "sharpe_net": s, "sharpe_ci95": [lo, hi],
           "sharpe_before_costs": sharpe(daily["gross_ret"].to_numpy()),
           "mean_daily_net_bp": float(r.mean() * 1e4), "mean_daily_before_costs_bp": float(daily["gross_ret"].mean() * 1e4),
           "turnover_per_day_x_equity": daily.attrs["turnover_per_day"], "held_share": daily.attrs["held_share"],
           "years_net": years, "max_dd": float(min(eq / np.maximum.accumulate(eq) - 1)),
           "final_coef": dict(zip(me.FEATURES, model.coef.round(5).tolist())),
           "result": "pass" if passed else "fail"}
    (BACKEND / "results" / "e1_ensemble.json").write_text(json.dumps(out, indent=2))
    live = BACKEND / "results" / "forward" / "algo"
    live.mkdir(parents=True, exist_ok=True)
    (live / "model.json").write_text(json.dumps({"trial": TRIAL, "result": out["result"], "features": me.FEATURES,
                                                 "fit_through": str(OOS_TO), **model.to_json()}, indent=2))
    register({"trial": TRIAL, "date": datetime.now(UTC).date().isoformat(), "kind": "daytrade", "sharpe_ann": round(s, 3),
              "window": f"{OOS_FROM}..{OOS_TO}", "result": out["result"]})
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
