import numpy as np
import pandas as pd

from scripts.desktop_export import curve


def test_curve_stats_match_returns():
    idx = pd.bdate_range("2020-01-01", periods=504)
    ret = pd.Series(np.r_[np.full(252, 0.001), np.full(252, -0.0005)], index=idx)
    c = curve(ret)
    assert c["equity"][0] > 100 and len(c["dates"]) == len(c["drawdown"]) == len(c["rolling_sharpe"])
    assert abs(c["stats"]["max_dd"] - ((1 - 0.0005) ** 252 - 1)) < 1e-3
    assert c["yearly"]["2020"] > 0 and c["stats"]["start"] == "2020-01-01"


def test_tearsheet_and_cone_are_consistent():
    from scripts.desktop_export import bootstrap_cone, drawdown_periods, tearsheet
    rng = np.random.default_rng(1)
    idx = pd.bdate_range("2020-01-01", periods=756)
    bench = pd.Series(rng.normal(0.0004, 0.01, 756), index=idx)
    ret = 0.5 * bench + pd.Series(rng.normal(0.0002, 0.004, 756), index=idx)
    t = tearsheet(ret, bench)
    assert 0.3 < t["stats"]["beta"] < 0.7 and t["stats"]["cvar95"] <= t["stats"]["var95"] < 0
    assert len(t["monthly"]["cells"]) == 3 and all(len(r) == 12 for r in t["monthly"]["cells"])
    assert sum(t["month_hist"]["counts"]) == sum(v is not None for r in t["monthly"]["cells"] for v in r)
    c = bootstrap_cone(ret)
    assert c["p5"][-1] < c["p50"][-1] < c["p95"][-1] and c["p50"][0] == 100.0 and len(c["months"]) == 13
    assert c == bootstrap_cone(ret)  # seeded: the same picture every rebuild
    dd = drawdown_periods(pd.Series([0.1, -0.2, -0.1, 0.5, -0.05], index=idx[:5]))
    assert dd[0]["depth"] == round(0.8 * 0.9 - 1, 4) and dd[0]["recovered"] == idx[3].strftime("%Y-%m-%d")
    assert dd[1]["recovered"] is None
