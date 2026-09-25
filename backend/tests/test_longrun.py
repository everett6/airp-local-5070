"""16-year walk-forward: no signal can depend on a price after its decision day; universes change by year."""
from datetime import date

import numpy as np
import pandas as pd

from app.sandbox.longrun import ARMS, Panel, bars_from_panel, feature_frames, signals


def _panel(seed=0, n_days=900, tickers=("A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L"), shock_from=None):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2010-01-04", periods=n_days)
    rets = rng.normal(0.0003, 0.015, size=(n_days, len(tickers) + 1))
    if shock_from is not None:  # rewrite everything after `shock_from`: the future changes, the past must not
        k = days.searchsorted(pd.Timestamp(shock_from), side="right")
        rets[k:] = np.random.default_rng(seed + 99).normal(-0.01, 0.05, size=rets[k:].shape)
    close = pd.DataFrame(100 * np.cumprod(1 + rets, axis=0), index=days, columns=[*tickers, "SPY"])
    opn = close.shift(1).fillna(100.0) * 1.001
    uni = {2010: set(tickers[:10]), 2011: set(tickers[2:]), 2012: set(tickers[2:]), 2013: set(tickers)}
    return Panel(open=opn, close=close, universe=uni)


def test_features_ignore_prices_after_the_day():
    a, b = _panel(), _panel(shock_from="2011-06-01")
    fa, fb = feature_frames(a.close), feature_frames(b.close)
    for k in fa:
        pd.testing.assert_frame_equal(fa[k].loc[:"2011-06-01"], fb[k].loc[:"2011-06-01"])
        assert not fa[k].loc["2011-07-01":].equals(fb[k].loc["2011-07-01":])


def test_no_signal_changes_when_only_the_future_changes():
    cut = date(2012, 3, 1)
    kw = {"start": date(2011, 1, 1), "end": date(2013, 6, 1), "min_train_weeks": 20, "refit_every": 4}
    sa, sb = signals(_panel(), **kw), signals(_panel(shock_from=cut.isoformat()), **kw)
    assert set(sa) == set(ARMS) and sa["feat_logit"], "the learned strategy must actually produce signals"
    for arm in ARMS:
        before_a = [r for r in sa[arm] if r["cutoff"] <= cut]
        before_b = [r for r in sb[arm] if r["cutoff"] <= cut]
        assert before_a == before_b, arm
        assert [r for r in sa[arm] if r["cutoff"] > cut] != [r for r in sb[arm] if r["cutoff"] > cut], arm


def test_only_that_years_universe_is_scored():
    s = signals(_panel(), start=date(2011, 1, 1), end=date(2013, 6, 1), min_train_weeks=20)
    for arm in ARMS:
        for r in s[arm]:
            if r["cutoff"].year == 2011:
                assert r["ticker"] not in ("A", "B")
            if r["cutoff"].year == 2010:
                raise AssertionError("warm-up year must not be traded")
    assert {r["ticker"] for r in s["momentum"] if r["cutoff"].year == 2013} >= {"A", "K", "L"}


def test_bars_from_panel_skip_missing_days():
    p = _panel()
    p.close.loc[p.close.index[5], "A"] = np.nan
    bars = bars_from_panel(p)
    assert len(bars["A"]) == len(p.close) - 1 and bars["A"][0][0] == date(2010, 1, 4)
