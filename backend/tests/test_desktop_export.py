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
