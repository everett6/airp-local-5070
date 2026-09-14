"""Cross-sectional scoring on synthetic data with a known rank IC."""
from datetime import date, timedelta

import numpy as np
import pytest

from app.sandbox.scoring import cross_sectional, paired_ic_gap, spearman


def test_spearman_matches_known_values_and_ties():
    assert spearman(np.array([1, 2, 3, 4.0]), np.array([10, 20, 30, 40.0])) == pytest.approx(1.0)
    assert spearman(np.array([1, 2, 3, 4.0]), np.array([4, 3, 2, 1.0])) == pytest.approx(-1.0)
    assert spearman(np.array([0.5, 0.5, 0.5]), np.array([1, 2, 3.0])) is None  # constant forecast: undefined
    from scipy.stats import spearmanr  # scipy is installed for the quant engine
    rng = np.random.default_rng(0)
    a, b = np.round(rng.normal(size=50), 1), rng.normal(size=50)  # rounding creates ties
    assert spearman(a, b) == pytest.approx(spearmanr(a, b).statistic, abs=1e-12)


def _preds(ic_target, weeks=40, names=100, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for w in range(weeks):
        c = date(2025, 6, 2) + timedelta(days=7 * w)
        f = rng.normal(size=names)
        ret = ic_target * f + np.sqrt(1 - ic_target ** 2) * rng.normal(size=names)
        rows += [{"cutoff": c, "p": float(x), "ret": float(y)} for x, y in zip(f, ret, strict=True)]
    return rows


def test_recovers_a_known_rank_ic_with_a_ci():
    s = cross_sectional(_preds(0.3))
    assert 0.24 < s["rank_ic_mean"] < 0.36 and s["rank_ic_lo"] > 0 and s["q_spread_pct"] > 0
    z = cross_sectional(_preds(0.0, seed=1))
    assert z["rank_ic_lo"] < 0 < z["rank_ic_hi"]


def test_paired_gap_and_small_cross_sections():
    good, noise = _preds(0.3, seed=2), _preds(0.0, seed=3)
    for n in noise:
        n["cutoff"] = n["cutoff"]
    g = paired_ic_gap(good, noise)
    assert g["gap"] > 0.2 and g["lo"] > 0
    assert cross_sectional(_preds(0.3, names=5))["rank_ic_mean"] is None  # under 10 names per week
