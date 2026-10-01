"""Weekly-review trading-health numbers (scripts/trading_health.py), synthetic data only."""
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from trading_health import execution, stage4, stage4_row


def test_stage4_rows_follow_the_plan_table():
    assert stage4_row(1.3).startswith("42%") and stage4_row(1.0) == "35% vol"
    assert stage4_row(0.7) == "25–30% vol" and stage4_row(-0.2) == "20% vol (1.0×)"


def test_stage4_needs_points_and_labels_early_rows_as_information():
    days = pd.date_range("2026-10-05", periods=20, freq="7D")  # about 4.5 months
    rng = np.random.default_rng(1)
    eq = pd.Series(100_000 * np.cumprod(1 + rng.normal(0.004, 0.01, 20)), index=days)
    rf = pd.Series(0.04, index=pd.date_range("2026-01-01", "2027-12-31"))
    out = stage4(eq, rf, start=date(2026, 10, 5))
    assert out["weeks"] == 19 and out["lower80"] < out["sharpe"]
    assert "information only" in out["stage4_row"]  # month 6 not reached
    assert "note" in stage4(eq.iloc[:5], rf) and "lower80" not in stage4(eq.iloc[:5], rf)
    assert stage4(eq.iloc[:1], rf)["points"] == 1


def test_stage4_excess_uses_the_days_between_runs():
    days = pd.to_datetime(["2026-10-05", "2026-10-12"])
    out = stage4(pd.Series([100.0, 101.0], index=days), pd.Series(0.0365, index=days))
    assert out["return_pct"] == 1.0 and out["max_drawdown_pct"] == 0.0


def test_execution_counts_status_gaps_and_audit_flags():
    orders = {"d1": {"legs": [{"status": "filled", "gap": 0.001}, {"status": "rejected", "gap": None}]}}
    ai = {"broker_audit_status": "checked", "pairs": [{"ticker": "JBL", "audit_flags": ["missing hedge"],
          "legs": [{"status": "filled", "gap": -0.008}, {"status": "submitted"}]}]}
    out = execution(orders, ai)
    assert out["legs"] == 4 and out["by_status"] == {"filled": 2, "rejected": 1, "submitted": 1}
    assert out["mean_abs_gap_bp"] == 45.0 and out["worst_gap_bp"] == 80.0 and out["gaps_over_alert"] == 1
    assert out["problem_legs"] == 1 and out["ai_audit_flags"] == 1
    assert execution({}, {})["legs"] == 0
