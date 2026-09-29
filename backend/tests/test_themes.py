import numpy as np
import pandas as pd

from app.portfolio import themes as T


def test_quarters_from_ytd_and_annual():
    e = [{"form": "10-Q", "start": "2025-01-01", "end": "2025-03-31", "val": 10},
         {"form": "10-Q", "start": "2025-01-01", "end": "2025-06-30", "val": 25},
         {"form": "10-Q", "start": "2025-01-01", "end": "2025-09-30", "val": 45},
         {"form": "10-K", "start": "2025-01-01", "end": "2025-12-31", "val": 70},
         {"form": "8-K", "start": "2025-01-01", "end": "2025-12-31", "val": 999}]
    q = T.quarters(e)
    assert q.tolist() == [10, 15, 20, 25] and T.ttm(q) == 70 and T.ttm(q, 1) is None


def test_ttm_needs_consecutive_quarters():
    q = pd.Series([1.0, 1, 1, 1], index=pd.to_datetime(["2024-03-31", "2024-06-30", "2024-12-31", "2025-03-31"]))
    assert T.ttm(q) is None


def _rated():
    rows = []
    for th in T.THEMES:
        rows.append({"key": th.key, "horizon": th.horizon, "ai_linked": th.ai_linked, "rating": 3, "mom": 0.0})
    by = {r["key"]: r for r in rows}
    by["semis"] |= {"rating": 5, "mom": 0.5}
    by["hyperscalers"] |= {"rating": 4, "mom": 0.1}
    by["cyber"] |= {"rating": 4, "mom": 0.3}
    by["biotech"] |= {"rating": 4, "mom": -0.2}
    by["solar"]["mom"] = 0.9
    return rows


def test_choose_rating_then_momentum_and_bubble_cap():
    r = _rated()
    assert T.choose(r, "medium", "elevated") == ["semis", "cyber"]
    assert T.choose(r, "medium", "high") == ["cyber"]  # AI-linked themes out
    assert T.choose(r, "long", "low") == ["biotech"]  # only 4+ ratings
    assert T.momentum_baseline(r, "long")[0] == "solar" and T.momentum_baseline(r, "medium")[0] == "semis"


def test_score_uses_hold_and_costs():
    idx = pd.bdate_range("2026-10-01", periods=300)
    opens = pd.DataFrame({"SPY": np.linspace(100, 110, 300), "SMH": np.linspace(100, 130, 300),
                          "CIBR": 100.0, "XBI": 100.0, "TAN": 100.0}, index=idx)
    c = {"type": "cohort", "month": "2026-10", "made_on": "2026-10-01",
         "picks": {"medium": ["semis"], "long": []}, "baseline": {"medium": ["cyber"], "long": ["solar"]}}
    res = {r["horizon"]: r for r in T.score([c], opens)}
    i, j = 1, 1 + 126
    spy = opens["SPY"].iloc[j] / opens["SPY"].iloc[i] - 1
    smh = opens["SMH"].iloc[j] / opens["SMH"].iloc[i] - 1
    assert abs(res["medium"]["excess_net"] - round(smh - spy - 2 * T.COST, 5)) < 1e-9
    assert res["long"]["excess_net"] == 0.0  # nothing picked: stays in SPY, no costs
    assert abs(res["long"]["baseline_excess_net"] - round(-(opens["SPY"].iloc[253] / opens["SPY"].iloc[1] - 1)
                                                          - 2 * T.COST, 5)) < 1e-9
    assert T.score([c, {"type": "result", "month": "2026-10", "horizon": "medium"}], opens)[0]["horizon"] == "long"


def test_stats_and_card():
    idx = pd.bdate_range("2025-01-01", periods=300)
    s = pd.Series(np.linspace(100, 160, 300), index=idx)
    spy = pd.Series(np.linspace(100, 110, 300), index=idx)
    st = T.stats(s, spy)
    assert st["r12"] > 0 and st["dd"] == 0 and st["vs200"] > 0 and st["mom"] > 0
    c = T.card(T.THEMES[0], st, ["VIX: 14.2"])
    assert "Holding period if picked: 6 months" in c and c.endswith("VIX: 14.2")
