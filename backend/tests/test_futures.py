from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.portfolio.futures import MBT, MES, FutureSpec, fut_price, next_expiry, simulate_futures

FLAT = FutureSpec("X", "U", 1.0, 1.0, 0.10, 0.08, "quarterly", carry_extra=0.0, cost_bps=0.0)


def frames(prices: list[float], start: str = "2024-01-02") -> tuple[pd.DataFrame, pd.DataFrame, pd.DatetimeIndex]:
    idx = pd.bdate_range(start, periods=len(prices))
    df = pd.DataFrame({"U": prices}, index=idx)
    return df, df, idx


def test_expiry_calendars():
    assert next_expiry(MES, date(2024, 1, 2)) == date(2024, 3, 15)  # third Friday of March
    assert next_expiry(MES, date(2024, 3, 10)) == date(2024, 6, 21)  # inside the roll window: next quarter
    assert next_expiry(MBT, date(2024, 1, 2)) == date(2024, 1, 26)  # last Friday of January


def test_futures_converge_to_spot_at_expiry():
    assert fut_price(MES, 500.0, 0.05, date(2024, 3, 15), date(2024, 3, 15)) == pytest.approx(5000.0)
    assert fut_price(MBT, 60_000.0, 0.05, date(2024, 1, 1), date(2024, 12, 31)) > 60_000.0 * 1.09


def test_no_position_earns_the_risk_free_rate():
    o, c, idx = frames([100.0] * 253)
    sim = simulate_futures({}, o, c, pd.Series(0.05, index=idx), [FLAT], idx[0].date(), idx[-1].date(), 1e6)
    days = (idx[-1] - idx[0]).days
    assert sim.equity[-1] == pytest.approx(1e6 * (1 + 0.05 / 365) ** days, rel=1e-3)


def test_settlement_moves_price_changes_into_cash():
    o, c, idx = frames([100.0, 100.0, 110.0, 99.0])
    sim = simulate_futures({idx[0].date(): {"X": 1.0}}, o, c, pd.Series(0.0, index=idx), [FLAT],
                           idx[0].date(), idx[-1].date(), 1000.0)
    # 10 contracts bought at 100 on day 1's open; +10 then -11 points
    assert sim.equity == pytest.approx([1000.0, 1000.0, 1100.0, 990.0])


def test_fully_collateralized_futures_track_spot_net_of_carry():
    # rf 5%, no dividends: the futures price decays towards spot, the cash earns rf; together ~ the spot return
    n = 60
    o, c, idx = frames(list(np.linspace(100, 110, n)))
    spec = FutureSpec("X", "U", 1.0, 1.0, 0.10, 0.08, "quarterly", carry_extra=0.0, cost_bps=0.0, roll_days=0)
    sim = simulate_futures({idx[0].date(): {"X": 1.0}}, o, c, pd.Series(0.05, index=idx), [spec],
                           idx[0].date(), idx[-1].date(), 1e6)
    spot = 110 / (100 + 10 / (n - 1)) - 1  # from the fill at day 1's open
    assert sim.equity[-1] / 1e6 - 1 == pytest.approx(spot, abs=0.004)


def test_rolls_follow_the_cycle():
    o, c, idx = frames([100.0] * 260)
    sim = simulate_futures({idx[0].date(): {"X": 1.0}}, o, c, pd.Series(0.0, index=idx), [FLAT],
                           idx[0].date(), idx[-1].date(), 1e6)
    assert sim.rolls == 4
    assert all(v == 10_000 for v in (e["contracts"]["X"] for e in sim.log))


def test_margin_call_cuts_a_levered_position():
    o, c, idx = frames([100.0, 100.0, 95.0, 95.0])
    sim = simulate_futures({idx[0].date(): {"X": 8.0}}, o, c, pd.Series(0.0, index=idx), [FLAT],
                           idx[0].date(), idx[-1].date(), 1000.0)
    assert len(sim.margin_calls) == 1  # 80 contracts, -5 points: equity 600 < maintenance 608
    assert sim.margin_calls[0]["contracts"]["X"] == (80, 63)
    assert not sim.blown_up


def test_a_big_enough_loss_ends_the_run():
    o, c, idx = frames([100.0, 100.0, 80.0, 80.0])
    sim = simulate_futures({idx[0].date(): {"X": 8.0}}, o, c, pd.Series(0.0, index=idx), [FLAT],
                           idx[0].date(), idx[-1].date(), 1000.0)
    assert sim.blown_up and sim.equity[-1] <= 0
