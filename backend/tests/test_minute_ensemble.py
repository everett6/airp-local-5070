import numpy as np
import pandas as pd

from app.sandbox import minute_ensemble as me


def bars(days: int = 30, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = []
    for d in pd.bdate_range("2024-03-04", periods=days):
        idx += list(pd.date_range(d + pd.Timedelta(minutes=570), periods=390, freq="min", tz=None))
    ts = pd.DatetimeIndex(idx).tz_localize("America/New_York")
    c = 100 * np.exp(np.cumsum(rng.normal(0, 5e-4, len(ts))))
    return pd.DataFrame({"ts": ts, "open": c, "high": c * 1.0002, "low": c * 0.9998, "close": c,
                         "volume": rng.integers(100, 1000, len(ts)).astype(float)})


def test_frame_has_no_lookahead_and_fills_missing_minutes():
    a, lead = bars(seed=1), bars(seed=2)
    a = a.drop(index=[100, 101])  # two minutes without trades
    f = me.frame(a, lead)
    assert len(f) == 30 * 390
    cut = a["ts"].iloc[-200]
    g = me.frame(a[a["ts"] <= cut], lead[lead["ts"] <= cut])
    cols = list(me.FEATURES) + ["sigma"]
    pd.testing.assert_frame_equal(f.loc[:cut, cols], g.loc[:cut, cols])
    assert f["sigma"].iloc[:5 * 390].isna().all() and f["sigma"].iloc[-1] > 0


def test_ridge_recovers_signal_and_targets_respect_costs():
    rng = np.random.default_rng(3)
    x = rng.normal(size=(5000, 3))
    y = 0.5 * x[:, 0] - 0.2 * x[:, 2] + rng.normal(0, 0.1, 5000)
    m = me.Ridge.fit(x, y)
    assert np.allclose(m.coef, [0.5, 0, -0.2], atol=0.02)
    assert np.allclose(me.Ridge.from_json(m.to_json()).predict(x[:3]), m.predict(x[:3]))
    w = me.targets({"SPY": 1.0, "XLF": 0.01, "XLE": -2.0}, {"SPY": 1e-3, "XLF": 1e-3, "XLE": 1e-3}, gross=2.8)
    assert w.keys() == {"SPY", "XLE"} and np.isclose(w["SPY"], 0.2) and np.isclose(w["XLE"], -0.2)


def test_live_path_with_two_sessions_matches_full_history():
    a, lead = bars(seed=4), bars(seed=5)
    full = me.frame(a, lead)
    day = a["ts"].dt.date.iloc[-1]
    st = me.day_stats(a, lead, day)
    days = sorted(set(a["ts"].dt.date))[-2:]
    a2, l2 = a[a["ts"].dt.date.isin(days)], lead[lead["ts"].dt.date.isin(days)]
    live = me.frame(a2, l2, stats=st)
    cols = list(me.FEATURES) + ["sigma"]
    today = full.index.date == day
    pd.testing.assert_frame_equal(full.loc[today, cols], live.loc[live.index.date == day, cols])
