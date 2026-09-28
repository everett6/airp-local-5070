from datetime import date

import numpy as np
import pandas as pd

from app.portfolio import planner as PL


def _returns(core=0.0008, event=0.0):
    idx = pd.bdate_range("2020-01-01", periods=1500)
    rng = np.random.default_rng(1)
    return pd.DataFrame({"core": core + 0.004 * rng.standard_normal(len(idx)),
                         "event": event + 0.005 * rng.standard_normal(len(idx))}, index=idx)


def test_required_cagr_and_ladder_weights():
    g = PL.Goal(10_000, 20_000, date(2030, 1, 1), date(2025, 1, 1))
    assert abs(g.required_cagr - (2 ** (1 / g.years) - 1)) < 1e-12
    w = PL.weights(PL.TRACKS)
    assert w["event"] == 0.10 and w["long_term"] == 0.0 and abs(w["core"] - 0.9) < 1e-12
    many = [PL.Track("a", "", "proven", ""), PL.Track("b", "", "proven", ""), PL.Track("c", "core", "core", "")]
    w2 = PL.weights(many)
    assert abs(w2["a"] + w2["b"] - PL.PICKING_CAP) < 1e-12 and abs(w2["core"] - 0.4) < 1e-12


def test_plan_odds_move_with_the_goal_and_never_change_weights():
    r = _returns()
    easy = PL.plan(PL.Goal(10_000, 10_500, date(2030, 1, 1), date(2025, 1, 1)), r)
    hard = PL.plan(PL.Goal(10_000, 400_000, date(2030, 1, 1), date(2025, 1, 1)), r)  # 109%/yr
    assert easy["with_current_evidence"]["p_goal"] > 0.8 > hard["with_current_evidence"]["p_goal"]
    assert easy["weights"] == hard["weights"]  # the goal never moves money
    assert hard["gap_cagr"] > 0.5 and any("doubling" in f for f in hard["flags"])
    assert any("pattern-day-trader" in f for f in hard["flags"])


def test_picking_need_is_consistent():
    r = _returns()
    res = PL.plan(PL.Goal(100_000, 300_000, date(2031, 1, 1), date(2026, 1, 1)), r)
    core = res["core_only"]["median_cagr"]
    need40 = res["picking_needs_cagr"]["40%"]
    assert abs(0.4 * need40 + 0.6 * core - res["goal"]["required_cagr"]) < 1e-3
