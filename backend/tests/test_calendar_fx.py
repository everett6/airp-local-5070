"""T1 turn-of-the-month helpers (app/sandbox/calendar_fx.py), synthetic data only."""
import numpy as np
import pandas as pd
import pytest

from app.sandbox.calendar_fx import diff_ci, tom_days, tom_overlay


def test_tom_days_last_one_and_first_three():
    idx = pd.bdate_range("2026-01-26", "2026-02-10")
    t = tom_days(idx)
    assert list(idx[t].strftime("%m-%d")) == ["01-30", "02-02", "02-03", "02-04"]  # cut-off edges not marked


def test_overlay_costs_on_entry_and_exit_only():
    idx = pd.bdate_range("2026-01-26", "2026-02-10")
    o = tom_overlay(pd.Series(0.01, index=idx), cost=1e-4)
    r = o["ret"]
    assert r["2026-01-30"] == pytest.approx(0.0099) and r["2026-02-02"] == pytest.approx(0.01)
    assert r["2026-02-04"] == pytest.approx(0.0099) and r["2026-02-05"] == 0.0 and r["2026-01-29"] == 0.0


def test_diff_ci_finds_a_planted_effect_and_not_a_missing_one():
    idx = pd.bdate_range("2010-01-01", periods=2500)
    tom = tom_days(idx)
    noise = pd.Series(np.random.default_rng(1).normal(0, 0.01, len(idx)), index=idx)
    point, lo, _ = diff_ci(noise + 0.003 * tom, tom, n=500)
    assert point == pytest.approx(0.003, abs=1e-3) and lo > 0
    _, lo0, hi0 = diff_ci(noise, tom, n=500)
    assert lo0 < 0 < hi0
