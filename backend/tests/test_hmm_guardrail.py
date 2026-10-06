import sys

import numpy as np
import pandas as pd
import pytest

from app.sandbox.hmm_regime import fit, panic_probability

sys.path.insert(0, "scripts")
import hmm_guardrail as h


def regimes(seed=1):
    rng = np.random.default_rng(seed)
    state = np.repeat([0, 1, 0, 1, 0], [600, 150, 500, 120, 400])
    x = np.where(state == 1, rng.normal(-0.002, 0.03, len(state)), rng.normal(0.0006, 0.008, len(state)))
    return x, state


def test_the_hmm_finds_the_volatile_state_and_flags_it():
    x, state = regimes()
    m = fit(x)
    assert (m.var[m.panic] / m.var[1 - m.panic]) ** 0.5 == pytest.approx(3.75, rel=0.2)
    p = panic_probability(x, m)
    assert ((p > 0.5) == (state == 1)).mean() > 0.95
    assert 1 < m.iterations <= 200


def test_book_costs_financing_and_timing():
    idx = pd.bdate_range("2024-01-01", periods=4)
    q = pd.Series([0.0, 0.01, 0.01, 0.01], index=idx)
    lev = pd.Series([2.0, 2.0, 0.5, 0.5], index=idx)
    bill = pd.Series(0.0252, index=idx)
    r = h.book(lev, q, bill)
    assert r.iloc[0] == pytest.approx(2 * 0.01 - (0.0252 + 0.005) / 252)  # leverage of close 1 earns day 2
    assert r.iloc[1] == pytest.approx(2 * 0.01 - (0.0252 + 0.005) / 252)
    assert r.iloc[2] == pytest.approx(0.5 * 0.01 + 0.5 * 0.0252 / 252 - 0.0002 * 1.5)  # cut at close 2, paid day 3


def test_fifty_day_rule_and_stats():
    close = pd.Series(np.r_[np.full(50, 100.0), [101.0, 99.0]], index=pd.bdate_range("2024-01-01", periods=52))
    assert list(h.fifty_day(close).iloc[-2:]) == [2.0, 1.0]
    r = pd.Series([0.1, -0.5, 0.2], index=pd.bdate_range("2024-01-01", periods=3))
    assert h.stats(r)["max_dd"] == pytest.approx(-0.5)
    lo, hi = h.bootstrap_diff(r.repeat(30).reset_index(drop=True), r.repeat(30).reset_index(drop=True), n=50)
    assert lo == hi == 0.0
