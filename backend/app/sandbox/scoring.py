"""Cross-sectional scores: does an arm RANK stocks correctly within each week, regardless of market direction?

  rank IC      Spearman correlation between the forecast and the realized return across stocks, per cutoff
  q spread     mean return of the top fifth by forecast minus the bottom fifth, per cutoff (before costs)

Both are summarized over cutoffs with a bootstrap that resamples whole cutoffs (weeks), because stocks in
the same week move together. When outcome windows overlap (horizon longer than the step, e.g. 20-day returns
forecast every 5 days) neighbouring weeks are correlated too, so the bootstrap resamples contiguous blocks of
`overlap_block(horizon, step)` weeks instead; for non-overlapping runs (block 1) it is the ordinary bootstrap. Ties in forecasts (e.g. rule baselines that output 0.49/0.51) get average
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


def overlap_block(horizon: int, step: int) -> int:
    """Number of consecutive cutoffs whose outcome windows overlap: 1 when horizon <= step."""
    return max(1, -(-int(horizon) // max(1, int(step))))


def periods_per_year(horizon: int) -> float:
    """Independent h-trading-day periods per year, on the 52 weeks x 5 days convention the published weekly
    Sharpes use (horizon 5 -> 52, horizon 20 -> 13)."""
    return 260.0 / max(1, int(horizon))


def boot_indices(rng: np.random.Generator, n: int, n_boot: int, block: int = 1) -> np.ndarray:
    """Bootstrap index draws over `n` time-ordered periods. block 1: the ordinary bootstrap (exactly the draw
    every published result used). block > 1: circular moving-block bootstrap (Kunsch 1989; Politis & Romano
    1992), which keeps the correlation between neighbouring periods that overlapping outcome windows create."""
    if block <= 1 or n <= 1:
        out: np.ndarray = rng.integers(0, n, size=(n_boot, n))
        return out
    b = min(int(block), n)
    k = -(-n // b)
    starts = rng.integers(0, n, size=(n_boot, k))
    idx: np.ndarray = ((starts[:, :, None] + np.arange(b)[None, None, :]) % n).reshape(n_boot, k * b)[:, :n]
    return idx


def _boot_ci(values: np.ndarray, n_boot: int = 2000, seed: int = 0, block: int = 1) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    idx = boot_indices(rng, len(values), n_boot, block)
    lo, hi = np.percentile(values[idx].mean(axis=1), [2.5, 97.5])
    return float(lo), float(hi)


def cross_sectional(preds: list[dict[str, Any]], key: str = "p", min_names: int = 10,
                    block: int = 1) -> dict[str, float | None]:
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
    ic_lo, ic_hi = _boot_ci(ic_arr, block=block)
    sp_lo, sp_hi = _boot_ci(sp_arr, block=block)
    return {"rank_ic_mean": round(float(ic_arr.mean()), 4), "rank_ic_lo": round(ic_lo, 4), "rank_ic_hi": round(ic_hi, 4),
            "rank_ic_weeks": len(ics), "q_spread_pct": round(float(sp_arr.mean()) * 100, 4),
            "q_spread_lo": round(sp_lo * 100, 4), "q_spread_hi": round(sp_hi * 100, 4)}


def paired_ic_gap(a: list[dict[str, Any]], b: list[dict[str, Any]], min_names: int = 10,
                  block: int = 1) -> dict[str, float] | None:
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
    lo, hi = _boot_ci(d, block=block)
    return {"gap": round(float(d.mean()), 4), "lo": round(lo, 4), "hi": round(hi, 4), "weeks": len(common)}


def _moments(r: np.ndarray) -> tuple[float, float, float]:
    """Per-period Sharpe, skewness, and (non-excess) kurtosis of a return series."""
    sd = float(r.std(ddof=1))
    if sd == 0 or len(r) < 3:
        return 0.0, 0.0, 3.0
    z = (r - r.mean()) / r.std(ddof=0)
    return float(r.mean() / sd), float(np.mean(z ** 3)), float(np.mean(z ** 4))


def probabilistic_sharpe(returns: list[float] | np.ndarray, sr_benchmark: float = 0.0,
                         block: int = 1) -> float | None:
    """PSR (Bailey & Lopez de Prado 2012): probability that the true per-period Sharpe exceeds `sr_benchmark`,
    given the sample length, skewness and kurtosis of the returns. With overlapping outcome windows the returns
    are autocorrelated, so the effective sample length is T / block."""
    from statistics import NormalDist

    r = np.asarray(returns, dtype=float)
    if len(r) < 3:
        return None
    sr, skew, kurt = _moments(r)
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if denom <= 0:
        return None
    t_eff = max(3.0, len(r) / max(1, block))
    return float(NormalDist().cdf((sr - sr_benchmark) * np.sqrt(t_eff - 1) / np.sqrt(denom)))


def deflated_sharpe(returns: list[float] | np.ndarray, n_trials: int, trial_sharpes: list[float] | None = None,
                    block: int = 1, horizon: int = 5) -> dict[str, float | None]:
    """DSR (Bailey & Lopez de Prado 2014): PSR against the Sharpe the best of `n_trials` unskilled strategies would
    reach by luck. The spread of Sharpes across trials comes from `trial_sharpes` (per-period) when there are at
    least 2, otherwise from the null sampling variance 1/(T_eff-1). Returns the per-period and annualized benchmark
    too (annualized with `periods_per_year(horizon)`: an h-day return per period)."""
    from statistics import NormalDist

    r = np.asarray(returns, dtype=float)
    if len(r) < 3 or n_trials < 1:
        return {"dsr": None, "sr0_per_period": None, "sr0_annual": None}
    if trial_sharpes is not None and len(trial_sharpes) >= 2:
        var = float(np.var(trial_sharpes, ddof=1))
    else:
        var = 1.0 / (max(3.0, len(r) / max(1, block)) - 1)
    nd, gamma = NormalDist(), 0.5772156649
    if n_trials == 1:
        sr0 = 0.0
    else:
        sr0 = float(np.sqrt(var) * ((1 - gamma) * nd.inv_cdf(1 - 1 / n_trials)
                                    + gamma * nd.inv_cdf(1 - 1 / (n_trials * np.e))))
    return {"dsr": probabilistic_sharpe(r, sr0, block=block), "sr0_per_period": round(sr0, 4),
            "sr0_annual": round(sr0 * float(np.sqrt(periods_per_year(horizon))), 3)}


def weekly_long_short(preds: list[dict[str, Any]], key: str = "p") -> list[float]:
    """Equal-weight long/short by the sign of each call per cutoff (same rule as walkforward.score), in cutoff order."""
    by: dict[Any, list[float]] = {}
    for p in preds:
        if p[key] != 0.5:
            by.setdefault(p["cutoff"], []).append((1.0 if p[key] > 0.5 else -1.0) * p["ret"])
    return [float(np.mean(v)) for _, v in sorted(by.items(), key=lambda kv: kv[0])]
