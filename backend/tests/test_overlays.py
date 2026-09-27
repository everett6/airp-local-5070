import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import numpy as np
import pandas as pd
from drawdown_brakes import brake
from spike_cause_briefs import check
from vol_target_b0 import CAP, managed


def test_label_needs_a_real_source():
    tags = {"S1": "https://example.com/a"}
    assert check({"label": "macro", "source": "S1"}, tags, False)["label"] == "macro"
    assert check({"label": "sector", "source": "S7"}, tags, False)["label"] == "unexplained"
    assert check({"label": "sector", "source": ""}, tags, False)["label"] == "unexplained"
    assert check({"label": "nonsense", "source": "S1"}, tags, False)["label"] == "unexplained"
    assert check(None, tags, False)["label"] == "unexplained"


def test_earnings_label_needs_a_release_near_the_day():
    tags = {"S1": "u"}
    assert check({"label": "earnings", "source": "S1"}, tags, False)["label"] == "unexplained"
    assert check({"label": "earnings", "source": "[s1]"}, tags, True)["label"] == "earnings"


def test_vol_target_caps_exposure_and_uses_only_past_vol():
    idx = pd.bdate_range("2020-01-01", periods=120)
    r = pd.Series(np.r_[np.full(60, 0.0005), np.random.default_rng(0).normal(0, 0.05, 60)], index=idx)
    _, ex = managed(r, pd.Series(0.0, index=idx))
    assert ex.max() <= CAP + 1e-12
    assert ex.iloc[:21].eq(1.0).all()  # no vol estimate yet: exposure stays at 1
    assert ex.iloc[-1] < 0.5  # 80% vol regime: cut
    changed = ex.diff().abs().iloc[1:]
    assert ((changed == 0) | (changed > 0.10)).all()  # the band: no small changes


def test_brake_cuts_after_a_drawdown():
    r = pd.Series([-0.06, -0.06, 0.0, 0.0], index=pd.bdate_range("2020-01-01", periods=4))
    _, m = brake(r, cost_bps=0)
    assert list(m) == [1.0, 1.0, 2 / 3, 2 / 3]
