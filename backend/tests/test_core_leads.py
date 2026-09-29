import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import core_leads


def _patch(monkeypatch, n: int):
    import skfolio_test
    import trend_sleeve
    import vol_target_b0
    idx = pd.bdate_range("2026-06-01", periods=n)
    r = pd.Series(np.random.default_rng(0).normal(0.0005, 0.01, n), index=idx)
    monkeypatch.setattr(trend_sleeve, "master_b0", lambda s, e: r)
    monkeypatch.setattr(vol_target_b0, "tbill", lambda: pd.Series(0.04, index=idx))
    monkeypatch.setattr(vol_target_b0, "managed", lambda b, rf: (b * 1.1, b * 0 + 1.1))
    monkeypatch.setattr(skfolio_test, "arm_s", lambda: (r * 0.9 + 0.0001, {}))


def test_only_forward_days_count(monkeypatch):
    _patch(monkeypatch, 90)  # 2026-06-01 .. early Oct: only a few days from 2026-09-30
    out = core_leads.forward(date(2026, 10, 10))
    assert out["days"] < 30 and "note" in out and not out["verdict_due"]


def test_compares_both_leads_after_30_days(monkeypatch):
    _patch(monkeypatch, 200)
    out = core_leads.forward(date(2027, 10, 1))
    assert out["days"] >= 30 and out["verdict_due"]
    assert set(out) >= {"vol_target", "risk_parity"} and "ci90" in out["vol_target"]
