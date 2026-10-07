"""E1 minute-bar signal ensemble (docs/PLAN_60_V2.md, "E1"): weak price signals on 1-minute bars, combined by one
pooled ridge regression. The backtest (scripts/e1_ensemble.py) and the live engine (scripts/algo_engine.py) build
features with the same `frame` so the live book trades exactly what was tested."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

UNIVERSE = ("SPY", "IWM", "DIA", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLC", "XLRE", "SMH")
FEATURES = ("r1", "r5", "r30", "lead1", "lead5", "resid5", "loc5", "volz", "vwap")
# E2 (30-minute models): E1's features plus these; xs_* are cross-sectional ranks added per decision by `ranks`
FEATURES_E2 = (*FEATURES, "r15", "r60", "rday", "gap", "rv30", "lead15", "lead30", "leadday", "resid30", "tod",
               "xs_r30", "xs_rday")
HOLD_E2 = 30
DECISION_BARS_E2 = tuple(range(29, 330, HOLD_E2))  # 10:00 .. 15:00 entries, flat by 15:30
HOLD = 5                                     # minutes per decision
DECISION_BARS = tuple(range(4, 380, HOLD))   # bar index (0 = 09:30) whose close triggers a decision: 09:34 .. 15:49
COST_SIDE = {"SPY": 0.5e-4}                  # per side; every other ETF 1.5 bp
DEFAULT_COST = 1.5e-4


def leader_of(symbol: str) -> str:
    return "QQQ" if symbol == "SPY" else "SPY"


def cost(symbol: str) -> float:
    return COST_SIDE.get(symbol, DEFAULT_COST)


def frame(bars: pd.DataFrame, lead: pd.DataFrame, stats: pd.DataFrame | None = None,
          with_rolled: bool = False) -> Any:
    """Features and the vol-scaled 5-minute forward target for every bar of one symbol.

    `bars` and `lead`: regular-hours 1-minute bars with ts (America/New_York), open, high, low, close, volume.
    Returns one row per bar of `bars` with FEATURES, sigma (prior-5-session 1-minute std), m (bar of the day),
    date, fwd (raw return open t+1 -> open t+6) and y (fwd / sigma). Nothing uses data after bar t except fwd/y."""
    df = bars.set_index("ts").sort_index()
    df = df[~df.index.duplicated()]
    # full 390-minute grid per session: a minute without trades keeps the last price and has zero volume
    days = pd.DatetimeIndex(sorted(set(df.index.normalize())))
    grid = (days.repeat(390) + pd.to_timedelta(np.tile(np.arange(570, 960), len(days)), unit="min"))
    grid = grid[grid <= df.index[-1]]
    df = df.reindex(grid)
    g0 = pd.Series(df.index.date, index=df.index)
    df["close"] = df["close"].groupby(g0).ffill()
    for col in ("open", "high", "low"):
        df[col] = df[col].fillna(df["close"])
    df["volume"] = df["volume"].fillna(0.0)
    df = df[df["close"].notna()]
    ld = lead.set_index("ts").sort_index()
    ld = ld[~ld.index.duplicated()]["close"].reindex(df.index).ffill()
    date = df.index.date
    m = (df.index.hour * 60 + df.index.minute - 570).to_numpy()
    g = pd.Series(date, index=df.index)
    lc, lo, lg = np.log(df["close"]), np.log(df["open"]), np.log(ld)

    def back(s: pd.Series, k: int) -> pd.Series:
        return (s - s.groupby(g).shift(k)).fillna(0.0)

    r1 = back(lc, 1)
    lr1 = back(lg, 1)
    rolled = None
    if stats is None:
        rolled = _rolled(date, r1, lr1)
        stats = rolled.shift(1)
    sigma = pd.Series(date, index=df.index).map(stats["sigma"]).astype(float)
    beta = pd.Series(date, index=df.index).map(stats["beta"]).astype(float).clip(-3, 3)

    r5, r30, lr5 = back(lc, 5), back(lc, 30), back(lg, 5)
    hi5 = df["high"].groupby(g).rolling(5, min_periods=1).max().reset_index(level=0, drop=True)
    lo5 = df["low"].groupby(g).rolling(5, min_periods=1).min().reset_index(level=0, drop=True)
    rng = (hi5 - lo5).replace(0, np.nan)
    loc5 = ((df["close"] - lo5) / rng - 0.5).fillna(0.0)
    v5 = df["volume"].groupby(g).rolling(5, min_periods=1).mean().reset_index(level=0, drop=True)
    vbase = df["volume"].rolling(390, min_periods=60).mean().shift(1)
    volz = (np.log((v5 + 1) / (vbase + 1)) * np.sign(r5)).fillna(0.0)
    pv = (df["close"] * df["volume"]).groupby(g).cumsum()
    vv = df["volume"].groupby(g).cumsum().replace(0, np.nan)
    vwap = (np.log(df["close"]) - np.log(pv / vv)).fillna(0.0)

    nxt1 = lo.groupby(g).shift(-1)
    nxt6 = lo.groupby(g).shift(-1 - HOLD)
    fwd = np.exp(nxt6 - nxt1) - 1
    fwd30 = np.exp(lo.groupby(g).shift(-1 - HOLD_E2) - nxt1) - 1
    first_open = lo.groupby(g).transform("first")
    lead_first = lg.groupby(g).transform("first")
    prev_close = pd.Series(date, index=df.index).map(lc.groupby(g).last().shift(1)).astype(float)
    r15, r60, lr15, lr30 = back(lc, 15), back(lc, 60), back(lg, 15), back(lg, 30)
    rv30 = np.sqrt((r1 * r1).groupby(g).rolling(30, min_periods=5).mean().reset_index(level=0, drop=True))
    out = pd.DataFrame({
        "date": date, "m": m, "sigma": sigma.to_numpy(),
        "r1": r1 / sigma, "r5": r5 / sigma, "r30": r30 / sigma, "lead1": lr1 / sigma, "lead5": lr5 / sigma,
        "resid5": (r5 - beta * lr5) / sigma, "loc5": loc5, "volz": volz, "vwap": vwap / sigma,
        "fwd": fwd, "y": fwd / sigma,
        "r15": r15 / sigma, "r60": r60 / sigma, "rday": (lc - first_open) / sigma,
        "gap": ((first_open - prev_close) / sigma).fillna(0.0), "rv30": (rv30 / sigma).fillna(1.0),
        "lead15": lr15 / sigma, "lead30": lr30 / sigma, "leadday": (lg - lead_first) / sigma,
        "resid30": (back(lc, 30) - beta * lr30) / sigma, "tod": m / 390.0,
        "fwd30": fwd30, "y30": fwd30 / sigma}, index=df.index)
    return (out, rolled) if with_rolled else out


def _rolled(date: np.ndarray, r1: pd.Series, lr1: pd.Series) -> pd.DataFrame:
    """Per session, including that session: 1-minute std over the last 5 sessions and beta over the last 20."""
    mom = pd.DataFrame({"d": date, "r": r1.to_numpy(), "l": lr1.to_numpy(), "rr": (r1 * r1).to_numpy(),
                        "ll": (lr1 * lr1).to_numpy(), "rl": (r1 * lr1).to_numpy()}).groupby("d").mean()
    var, lvar, cov = mom["rr"] - mom["r"] ** 2, mom["ll"] - mom["l"] ** 2, mom["rl"] - mom["r"] * mom["l"]
    return pd.DataFrame({"sigma": np.sqrt(var.rolling(5).mean()),
                         "beta": cov.rolling(20).sum() / lvar.rolling(20).sum()})


def day_stats(bars: pd.DataFrame, lead: pd.DataFrame, day: object) -> pd.DataFrame:
    """`stats` for `day` from bars of the sessions before it (21 or more), for the live engine."""
    _, rolled = frame(bars[bars["ts"].dt.date < day], lead[lead["ts"].dt.date < day], with_rolled=True)
    return pd.DataFrame({"sigma": [rolled["sigma"].iloc[-1]], "beta": [rolled["beta"].iloc[-1]]}, index=[day])


@dataclass
class Ridge:
    mean: np.ndarray
    std: np.ndarray
    coef: np.ndarray
    intercept: float

    @classmethod
    def fit(cls, x: np.ndarray, y: np.ndarray, alpha: float = 10.0) -> Ridge:
        mean, std = x.mean(0), x.std(0)
        std = np.where(std > 0, std, 1.0)
        z = (x - mean) / std
        yc = y - y.mean()
        coef = np.linalg.solve(z.T @ z + alpha * np.eye(z.shape[1]), z.T @ yc)
        return cls(mean, std, coef, float(y.mean()))

    def predict(self, x: np.ndarray) -> np.ndarray:
        return ((x - self.mean) / self.std) @ self.coef + self.intercept

    def to_json(self) -> dict[str, list[float] | float]:
        return {"mean": self.mean.tolist(), "std": self.std.tolist(), "coef": self.coef.tolist(),
                "intercept": self.intercept}

    @classmethod
    def from_json(cls, d: dict) -> Ridge:
        return cls(np.array(d["mean"]), np.array(d["std"]), np.array(d["coef"]), float(d["intercept"]))


def targets(pred: dict[str, float], sigma: dict[str, float], gross: float,
            universe: tuple[str, ...] = UNIVERSE) -> dict[str, float]:
    """Weights (fraction of equity) for one decision: sign of the predicted return where it beats the round trip."""
    w: dict[str, float] = {}
    for s in universe:
        p = pred.get(s)
        if p is None or not np.isfinite(p) or not sigma.get(s):
            continue
        ret = p * sigma[s]
        if abs(ret) > 2 * cost(s):
            w[s] = float(np.sign(ret)) * gross / len(universe)
    return w


def ranks(panel: pd.DataFrame) -> pd.DataFrame:
    """E2's cross-sectional features: rank (-0.5..0.5) of the 30-minute return and of the return since the open
    among the ETFs at the same bar."""
    grp = panel.groupby(["date", "m"])
    return panel.assign(xs_r30=grp["r30"].rank(pct=True) - 0.5, xs_rday=grp["rday"].rank(pct=True) - 0.5)
