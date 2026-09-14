"""Cross-sectional scores: does an arm RANK stocks correctly within each week, regardless of market direction?

  rank IC      Spearman correlation between the forecast and the realized return across stocks, per cutoff
  q spread     mean return of the top fifth by forecast minus the bottom fifth, per cutoff (before costs)

Both are summarized over cutoffs with a bootstrap that resamples whole cutoffs (weeks), because stocks in
the same week move together. Ties in forecasts (e.g. rule baselines that output 0.49/0.51) get average
ranks; a cutoff where the forecast doesn't vary has no defined IC and is skipped.
"""
from __future__ import annotations

from typing import Any

import numpy as np


def _rank(x: np.ndarray) -> np.ndarray:
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x))
    ranks[order] = np.arange(len(x), dtype=float)
    # average ranks for ties
    _, inv, counts = np.unique(x, return_inverse=True, return_counts=True)
    sums = np.bincount(inv, weights=ranks)
    out: np.ndarray = sums[inv] / counts[inv]
    return out


def spearman(a: np.ndarray, b: np.ndarray) -> float | None:
    if len(a) < 3 or np.ptp(a) == 0 or np.ptp(b) == 0:
        return None
    ra, rb = _rank(a), _rank(b)
    return float(np.corrcoef(ra, rb)[0, 1])


def _boot_ci(values: np.ndarray, n_boot: int = 2000, seed: int = 0) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(values), size=(n_boot, len(values)))
    lo, hi = np.percentile(values[idx].mean(axis=1), [2.5, 97.5])
    return float(lo), float(hi)


def cross_sectional(preds: list[dict[str, Any]], key: str = "p", min_names: int = 10) -> dict[str, float | None]:
    by_cut: dict[Any, list[tuple[float, float]]] = {}
    for p in preds:
        by_cut.setdefault(p["cutoff"], []).append((p[key], p["ret"]))
    ics, spreads = [], []
    for _, rows in sorted(by_cut.items(), key=lambda kv: kv[0]):
        if len(rows) < min_names:
            continue
        f = np.array([r[0] for r in rows])
        r = np.array([r[1] for r in rows])
        ic = spearman(f, r)
        if ic is None:
            continue
        ics.append(ic)
        k = max(1, len(rows) // 5)
        order = np.argsort(f, kind="mergesort")
        spreads.append(float(r[order[-k:]].mean() - r[order[:k]].mean()))
    if len(ics) < 2:
        return {"rank_ic_mean": None, "rank_ic_lo": None, "rank_ic_hi": None, "rank_ic_weeks": len(ics),
                "q_spread_pct": None, "q_spread_lo": None, "q_spread_hi": None}
    ic_arr, sp_arr = np.array(ics), np.array(spreads)
    ic_lo, ic_hi = _boot_ci(ic_arr)
    sp_lo, sp_hi = _boot_ci(sp_arr)
    return {"rank_ic_mean": round(float(ic_arr.mean()), 4), "rank_ic_lo": round(ic_lo, 4), "rank_ic_hi": round(ic_hi, 4),
            "rank_ic_weeks": len(ics), "q_spread_pct": round(float(sp_arr.mean()) * 100, 4),
            "q_spread_lo": round(sp_lo * 100, 4), "q_spread_hi": round(sp_hi * 100, 4)}


def paired_ic_gap(a: list[dict[str, Any]], b: list[dict[str, Any]], min_names: int = 10) -> dict[str, float] | None:
    """Mean per-week difference in rank IC between two arms on the same weeks, with a week-bootstrap CI."""
    def per_week(preds: list[dict[str, Any]]) -> dict[Any, float]:
        by: dict[Any, list[tuple[float, float]]] = {}
        for p in preds:
            by.setdefault(p["cutoff"], []).append((p["p"], p["ret"]))
        out = {}
        for c, rows in by.items():
            if len(rows) >= min_names:
                ic = spearman(np.array([x[0] for x in rows]), np.array([x[1] for x in rows]))
                if ic is not None:
                    out[c] = ic
        return out
    wa, wb = per_week(a), per_week(b)
    common = sorted(set(wa) & set(wb))
    if len(common) < 2:
        return None
    d = np.array([wa[c] - wb[c] for c in common])
    lo, hi = _boot_ci(d)
    return {"gap": round(float(d.mean()), 4), "lo": round(lo, 4), "hi": round(hi, 4), "weeks": len(common)}
