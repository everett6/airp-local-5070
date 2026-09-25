"""
16-year walk-forward signals on a universe that changes every year (point-in-time S&P 500 top 100).

Every week (every 5th trading day) each strategy scores the stocks that were in that year's universe, using
only bars dated on or before that day; the paper-trading simulator then buys the top picks at the next open.

Strategies (no language model; cheap enough for 16 years on a CPU):
  momentum      12-1 month return (Jegadeesh & Titman 1993)
  reversal      minus the last month's return (Jegadeesh 1990)
  low_vol       minus 60-day idiosyncratic volatility (Ang et al. 2006)
  high_52w      price / 52-week high (George & Hwang 2004)
  anomaly_rank  the mean of those four signed ranks (same definitions as app/sandbox/anomalies.py)
  feat_logit    logistic regression of "beats the week's median stock over the next 5 days" on the week's
                cross-sectional feature ranks,
                refitted every 13 weeks on the previous 3 years of RESOLVED weeks only (label known by then)

Point-in-time guarantees (tested in tests/test_longrun.py):
  - features on day d are rolling windows ending at d; moving any price after d cannot change them;
  - a training row from week w is used only once w + horizon <= the fit day, so its label was already known;
  - a stock is scored on day d only if it was in the universe chosen on Jan 1 of d's year and traded on d.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from app.learning.linear import fit_logistic_newton_np

FEATURES = ["mom_12_1", "rev_1m", "hi_52w", "ivol_60d", "ret_5d", "vol_20d", "ma_gap_50"]
SIGNED = {"momentum": ("mom_12_1", 1.0), "reversal": ("rev_1m", 1.0), "low_vol": ("ivol_60d", -1.0),
          "high_52w": ("hi_52w", 1.0)}
ARMS = [*SIGNED, "anomaly_rank", "feat_logit"]


@dataclass
class Panel:
    open: pd.DataFrame   # dates x tickers
    close: pd.DataFrame
    universe: dict[int, set[str]]  # year -> tickers chosen on Jan 1 of that year

    @classmethod
    def load(cls, ohlcv: pd.DataFrame, universe: pd.DataFrame) -> Panel:
        df = ohlcv.copy()
        df["Date"] = pd.to_datetime(df["Date"])
        wide_c = df.pivot(index="Date", columns="Ticker", values="Close").sort_index()
        wide_o = df.pivot(index="Date", columns="Ticker", values="Open").sort_index()
        spy_days = wide_c["SPY"].dropna().index
        uni = {int(str(y)): set(g["ticker"]) for y, g in universe.groupby("year")}
        return cls(open=wide_o.reindex(spy_days), close=wide_c.reindex(spy_days), universe=uni)


def feature_frames(close: pd.DataFrame, market: str = "SPY") -> dict[str, pd.DataFrame]:
    """Each frame's row d uses closes on or before d only (trailing windows; no centring, no backfill)."""
    c = close
    r = c.pct_change(fill_method=None)
    m = r[market]
    var_m = m.rolling(60, min_periods=40).var()
    cov = r.rolling(60, min_periods=40).cov(m)
    var_r = r.rolling(60, min_periods=40).var()
    beta = cov.div(var_m, axis=0)
    resid_var = (var_r - beta.pow(2).mul(var_m, axis=0)).clip(lower=0)
    return {
        "mom_12_1": c.shift(21) / c.shift(252) - 1,
        "rev_1m": -(c / c.shift(21) - 1),
        "hi_52w": c / c.rolling(252, min_periods=200).max(),
        "ivol_60d": np.sqrt(resid_var * 252),
        "ret_5d": c / c.shift(5) - 1,
        "vol_20d": r.rolling(20, min_periods=15).std() * np.sqrt(252),
        "ma_gap_50": c / c.rolling(50, min_periods=40).mean() - 1,
    }


def decision_days(index: pd.DatetimeIndex, start: date, end: date, step: int = 5) -> list[pd.Timestamp]:
    days = [d for d in index if start <= d.date() <= end]
    return days[::step]


def _ranks(x: pd.Series) -> pd.Series:
    return x.rank(pct=True, method="average")


def cross_section(panel: Panel, feats: dict[str, pd.DataFrame], d: pd.Timestamp) -> pd.DataFrame:
    """Feature values for the stocks in d's universe that traded on d and have every feature."""
    members = sorted(t for t in panel.universe.get(d.year, set()) if t in panel.close.columns)
    alive = [t for t in members if pd.notna(panel.close.at[d, t])]
    x = pd.DataFrame({k: feats[k].loc[d, alive] for k in FEATURES})
    return x.dropna()


def rank_matrix(x: pd.DataFrame) -> np.ndarray:
    """Cross-sectional percentile ranks centred on 0: comparable across years and immune to outliers."""
    return np.column_stack([_ranks(x[k]).to_numpy() - 0.5 for k in FEATURES])


def signals(panel: Panel, start: date, end: date, horizon: int = 5, step: int = 5, train_years: int = 3,
            refit_every: int = 13, min_train_weeks: int = 52, arms: Iterable[str] = ARMS
            ) -> dict[str, list[dict[str, Any]]]:
    arms = list(arms)
    feats = feature_frames(panel.close)
    idx = pd.DatetimeIndex(panel.close.index)
    pos = {d: i for i, d in enumerate(idx)}
    all_days = decision_days(idx, date(idx[0].year, 1, 1), end, step)
    out: dict[str, list[dict[str, Any]]] = {a: [] for a in arms}
    # training rows: (decision day, rank matrix, labels) for every past decision day
    history: list[tuple[pd.Timestamp, np.ndarray, np.ndarray]] = []
    w: np.ndarray | None = None
    weeks_since_fit = refit_every
    close = panel.close
    for d in all_days:
        x = cross_section(panel, feats, d)
        if len(x) < 10:
            continue
        rm = rank_matrix(x)
        i = pos[d]
        if i + horizon < len(idx):  # label: beat the median stock over `horizon` days (known at i + horizon)
            names = list(x.index)
            fwd = close.iloc[i + horizon][names].to_numpy() / close.iloc[i][names].to_numpy()
            ok = ~np.isnan(fwd)
            history.append((d, rm[ok], (fwd[ok] > np.median(fwd[ok])).astype(float)))
        if d.date() < start:
            continue
        # ---- rule arms: fixed signs, never fitted ----
        for arm, (key, sign) in SIGNED.items():
            if arm in out:
                sc = _ranks(sign * x[key])
                out[arm].extend({"cutoff": d.date(), "ticker": t, "p": 0.45 + 0.1 * float(v)} for t, v in sc.items())
        if "anomaly_rank" in out:
            comp = pd.concat([_ranks(s * x[k]) for k, s in SIGNED.values()], axis=1).mean(axis=1)
            out["anomaly_rank"].extend({"cutoff": d.date(), "ticker": t, "p": 0.45 + 0.1 * float(v)}
                                       for t, v in comp.items())
        # ---- feat_logit: refit on resolved weeks only ----
        if "feat_logit" in out:
            weeks_since_fit += 1
            if w is None or weeks_since_fit >= refit_every:
                lo = d - pd.DateOffset(years=train_years)
                # a week's label is known once its horizon has passed; `pos` is the trading-day index
                train = [(xs, ys) for (dd, xs, ys) in history if dd >= lo and pos[dd] + horizon <= i]
                if len(train) >= min_train_weeks:
                    xs = np.vstack([t[0] for t in train])
                    ys = np.concatenate([t[1] for t in train])
                    w = np.asarray(fit_logistic_newton_np(xs.tolist(), ys.astype(int).tolist(), l2=0.01))
                    weeks_since_fit = 0
            if w is not None:
                z = w[0] + rm @ w[1:]
                p = 1 / (1 + np.exp(-z))
                out["feat_logit"].extend({"cutoff": d.date(), "ticker": t, "p": float(v)}
                                         for t, v in zip(x.index, p, strict=True))
    return out


def bars_from_panel(panel: Panel) -> dict[str, list[tuple[date, float, float, float, float, float]]]:
    """Simulator bars (day, open, high, low, close, volume); high/low/volume are unused by it."""
    out: dict[str, list[tuple[date, float, float, float, float, float]]] = {}
    for t in panel.close.columns:
        o, c = panel.open[t], panel.close[t]
        ok = o.notna() & c.notna()
        out[t] = [(d.date(), float(oo), float(max(oo, cc)), float(min(oo, cc)), float(cc), 0.0)
                  for d, oo, cc in zip(o.index[ok], o[ok], c[ok], strict=True)]
    return out
