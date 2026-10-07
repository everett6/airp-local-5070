"""E2 custom day-trading models, run once (docs/PLAN_60_V2.md, "E2"): gradient-boosted trees (M-GBT) and a neural
network (M-NN) on 30-minute ETF moves. Writes results/e2_models.json, the registry lines and, for a model that
passes, results/forward/algo/model_e2_<name>.pkl for Autopilot Day.

    python scripts/e2_models.py
"""
from __future__ import annotations

import json
import pickle
import sys
from datetime import UTC, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np
import pandas as pd
from e1_ensemble import OOS_FROM, OOS_TO, TRAIN_FROM, load, sharpe
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from app.sandbox import minute_ensemble as me
from app.sandbox.dsr import register

GROSS = 3.0
LEVEL = 0.9875  # Bonferroni over two models
NN_ROWS = 400_000


def models() -> dict[str, object]:
    return {
        "gbt": HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=500,
                                             l2_regularization=1.0, random_state=7),
        "nn": make_pipeline(StandardScaler(), MLPRegressor(hidden_layer_sizes=(32, 16), alpha=1e-3, early_stopping=True,
                                                           max_iter=50, random_state=7)),
    }


def panel() -> pd.DataFrame:
    lead = {s: load(s) for s in ("SPY", "QQQ")}
    parts = []
    for s in me.UNIVERSE:
        f = me.frame(lead["SPY"] if s == "SPY" else load(s), lead[me.leader_of(s)])
        f.attrs = {}
        f = f[f["m"].isin(me.DECISION_BARS_E2) & (f["date"] >= TRAIN_FROM) & (f["date"] <= OOS_TO)]
        parts.append(f.assign(symbol=s))
    p = me.ranks(pd.concat(parts))
    ok = np.isfinite(p[["sigma", *me.FEATURES_E2]]).all(axis=1) & (p["sigma"] > 0)
    return p[ok]


def walk_forward(p: pd.DataFrame, name: str) -> pd.Series:
    x, y = p[list(me.FEATURES_E2)].to_numpy(), p["y30"].to_numpy()
    dates = pd.to_datetime(p["date"])
    pred = np.full(len(p), np.nan)
    rng = np.random.default_rng(7)
    for q in pd.period_range(OOS_FROM, OOS_TO, freq="Q"):
        train = ((dates < q.start_time) & np.isfinite(y)).to_numpy()
        idx = np.flatnonzero(train)
        if name == "nn" and len(idx) > NN_ROWS:
            idx = np.sort(rng.choice(idx, NN_ROWS, replace=False))
        model = models()[name]
        model.fit(x[idx], y[idx])
        rows = ((dates >= q.start_time) & (dates <= q.end_time)).to_numpy()
        if rows.any():
            pred[rows] = model.predict(x[rows])
        print(f"  {name} {q}: trained on {len(idx):,} rows", flush=True)
    return pd.Series(pred, index=p.index)


def book(p: pd.DataFrame, pred: np.ndarray) -> pd.DataFrame:
    """Daily net returns: hold sign(pred) where |pred x sigma| beats the round trip; contiguous 30-minute holds."""
    o = p.assign(pred=pred)
    o = o[o["pred"].notna() & o["fwd30"].notna()].copy()
    ret, c = o["pred"] * o["sigma"], o["symbol"].map(me.cost).astype(float)
    o["w"] = np.where(ret.abs() > 2 * c, np.sign(ret), 0.0) * GROSS / len(me.UNIVERSE)
    o = o.sort_values(["symbol", "date", "m"])
    prev = o.groupby(["symbol", "date"])["w"].shift(1).fillna(0.0)
    last = o.groupby(["symbol", "date"])["m"].transform("max") == o["m"]
    turnover = (o["w"] - prev).abs() + np.where(last, o["w"].abs(), 0.0)
    o["net"], o["gross_ret"] = o["w"] * o["fwd30"] - turnover * c, o["w"] * o["fwd30"]
    daily = o.groupby("date")[["net", "gross_ret"]].sum()
    daily.attrs["held_share"] = float((o["w"] != 0).mean())
    return daily


def verdict(daily: pd.DataFrame) -> dict[str, object]:
    r = daily["net"].to_numpy()
    rng = np.random.default_rng(7)
    boots = [sharpe(r[rng.integers(0, len(r), len(r))]) for _ in range(2000)]
    tail = (1 - LEVEL) / 2 * 100
    lo, hi = float(np.percentile(boots, tail)), float(np.percentile(boots, 100 - tail))
    years = {str(y): float(np.prod(1 + g["net"]) - 1) for y, g in daily.groupby(pd.to_datetime(daily.index).year)}
    s = sharpe(r)
    eq = np.cumprod(1 + r)
    return {"days": len(r), "sharpe_net": s, "sharpe_ci": [lo, hi], "ci_level": LEVEL,
            "sharpe_before_costs": sharpe(daily["gross_ret"].to_numpy()), "mean_daily_net_bp": float(r.mean() * 1e4),
            "held_share": daily.attrs["held_share"], "years_net": years, "max_dd": float(min(eq / np.maximum.accumulate(eq) - 1)),
            "result": "pass" if s >= 1.0 and lo > 0 and all(v > 0 for v in years.values()) else "fail"}


def main() -> None:
    p = panel()
    print(f"panel: {len(p):,} rows, {p['date'].nunique()} days", flush=True)
    out: dict[str, object] = {"trial": "day_models_e2", "run_at": datetime.now(UTC).isoformat(),
                              "oos": [str(OOS_FROM), str(OOS_TO)], "gross": GROSS, "models": {}}
    live = BACKEND / "results" / "forward" / "algo"
    for name in ("gbt", "nn"):
        v = verdict(book(p, walk_forward(p, name).to_numpy()))
        out["models"][name] = v  # type: ignore[index]
        register({"trial": f"day_models_e2_{name}", "date": datetime.now(UTC).date().isoformat(), "kind": "daytrade",
                  "sharpe_ann": round(v["sharpe_net"], 3), "window": f"{OOS_FROM}..{OOS_TO}", "result": v["result"]})
        if v["result"] == "pass":  # the final model on all data, for Autopilot Day
            x, y = p[list(me.FEATURES_E2)].to_numpy(), p["y30"].to_numpy()
            ok = np.isfinite(y)
            idx = np.flatnonzero(ok)
            if name == "nn" and len(idx) > NN_ROWS:
                idx = np.sort(np.random.default_rng(7).choice(idx, NN_ROWS, replace=False))
            m = models()[name]
            m.fit(x[idx], y[idx])
            live.mkdir(parents=True, exist_ok=True)
            with (live / f"model_e2_{name}.pkl").open("wb") as f:
                pickle.dump({"model": m, "features": me.FEATURES_E2, "sharpe": v["sharpe_net"]}, f)
        print(name, json.dumps(v), flush=True)
    (BACKEND / "results" / "e2_models.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
