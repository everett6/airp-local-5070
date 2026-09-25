"""Earnings-event timing: a release is traded only at the first open after SEC accepted it."""
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from app.sandbox.events import (
    Prices,
    check,
    entry_index,
    fwd_excess,
    monthly_ic,
    number_in_quote,
    quintile_spread,
    reaction,
)

DAYS = pd.DatetimeIndex(pd.bdate_range("2025-03-03", periods=30))  # Mon 3 Mar 2025 ...


@pytest.mark.parametrize("accepted,expected", [
    (datetime(2025, 3, 4, 10, 30, tzinfo=UTC), "2025-03-04"),   # 6:30 New York, before the open: same day
    (datetime(2025, 3, 4, 12, 59, tzinfo=UTC), "2025-03-04"),
    (datetime(2025, 3, 4, 13, 0, tzinfo=UTC), "2025-03-05"),    # too close to (or after) the open: next day
    (datetime(2025, 3, 4, 20, 30, tzinfo=UTC), "2025-03-05"),   # 16:30 New York, after the close
    (datetime(2025, 3, 8, 11, 0, tzinfo=UTC), "2025-03-10"),    # Saturday: Monday's open
])
def test_entry_is_the_first_open_after_acceptance(accepted, expected):
    assert DAYS[entry_index(DAYS, accepted)] == pd.Timestamp(expected)


def test_entry_beyond_the_data_is_none():
    assert entry_index(DAYS, datetime(2026, 1, 1, tzinfo=UTC)) is None


def _prices():
    o = pd.DataFrame({"A": np.linspace(100, 129, 30), "XLK": np.full(30, 50.0), "SPY": np.full(30, 400.0)}, index=DAYS)
    c = o * 1.01
    return Prices(open=o, close=c)


def test_forward_excess_is_open_to_open_minus_sector():
    p = _prices()
    assert fwd_excess(p, "A", "XLK", 0, 20) == pytest.approx(120 / 100 - 1)
    assert fwd_excess(p, "A", "XLK", 25, 20) is None      # horizon runs past the data
    assert fwd_excess(p, "ZZZ", "XLK", 0, 5) is None


def test_reaction_uses_closes_up_to_the_entry_day_only():
    p = _prices()
    r = reaction(p, "A", "XLK", 5)
    assert r == pytest.approx(p.close["A"].iloc[5] / p.close["A"].iloc[4] - 1)
    p.close.iloc[6:] = 1e9  # later prices must not matter
    assert reaction(p, "A", "XLK", 5) == pytest.approx(r)


def test_ic_and_spread_find_a_planted_signal_and_not_noise():
    rng = np.random.default_rng(0)
    n = 1200
    df = pd.DataFrame({"month": np.repeat(np.arange(24), n // 24), "sig": rng.normal(size=n)})
    df["out"] = 0.02 * df["sig"] + rng.normal(scale=0.05, size=n)
    df["noise"] = rng.normal(size=n)
    good, bad = monthly_ic(df, "sig", "out"), monthly_ic(df, "noise", "out")
    assert good["ci_lo"] > 0.2 and bad["ci_lo"] < 0 < bad["ci_hi"]
    assert quintile_spread(df, "sig", "out")["ci_lo"] > 0


RELEASE = ("Acme Corp reports first quarter results. Revenue was $3.4 billion, up 12% from $3,036 million a year "
           "ago. Diluted EPS of $1.25 compared with $(0.40) last year. The company raised its full-year outlook.")


def test_number_in_quote_handles_scale_and_negatives():
    assert number_in_quote(3400, "Revenue was $3.4 billion", scaled=True)
    assert number_in_quote(3036, "from $3,036 million", scaled=True)
    assert number_in_quote(-0.40, "compared with $(0.40) last year", scaled=False)
    assert not number_in_quote(3500, "Revenue was $3.4 billion", scaled=True)
    assert not number_in_quote(1.25, "Revenue was $3.4 billion", scaled=False)


def test_reader_check_keeps_only_numbers_quoted_from_the_release():
    raw = {"revenue": {"q": 3400, "prior": 3036, "quote": "Revenue was $3.4 billion, up 12% from $3,036 million"},
           "eps": {"q": 1.25, "prior": -0.40, "quote": "Diluted EPS of $1.25 compared with $(0.40) last year"},
           "adj_eps": {"q": 1.50, "prior": None, "quote": "Adjusted EPS of $1.50"},        # quote not in release
           "guidance": "raised", "guidance_quote": "The company raised its full-year outlook", "tone": "positive",
           "highlights": ["Revenue was $3.4 billion", "Record demand everywhere"]}
    out = check(raw, RELEASE)
    assert out["revenue"] == {"q": 3400.0, "prior": 3036.0} and out["eps"] == {"q": 1.25, "prior": -0.40}
    assert out["adj_eps"] == {} and out["rejected"] == ["adj_eps.q"]
    assert out["guidance"] == "raised" and out["highlights"] == ["Revenue was $3.4 billion"]
    wrong = check({**raw, "revenue": {"q": 3900, "quote": "Revenue was $3.4 billion"},
                   "guidance_quote": "Guidance raised a lot"}, RELEASE)
    assert wrong["revenue"] == {} and "revenue.q" in wrong["rejected"] and wrong["guidance"] == "unverified"
    assert check(None, RELEASE)["parsed"] is False
