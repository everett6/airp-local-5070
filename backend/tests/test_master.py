"""Master agent: calibration never sees the future, sizing respects every cap, trades happen the next open."""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from app.portfolio.master import (
    Calibrator,
    Candidate,
    MasterConfig,
    allocate,
    crypto_state,
    simulate_weights,
)


def test_calibrator_uses_only_outcomes_known_by_the_query_date():
    cal = Calibrator(min_rows=50)
    rng = np.random.default_rng(0)
    for i in range(300):
        s = float(rng.normal())
        cal.add(date(2025, 1, 1) + timedelta(days=i), s, bool(s + rng.normal(scale=0.5) > 0))
    assert cal.prob(1.0, date(2025, 1, 20)) is None             # fewer than 50 outcomes known yet
    hi, lo = cal.prob(2.0, date(2025, 12, 1)), cal.prob(-2.0, date(2025, 12, 1))
    assert hi > 0.8 and lo < 0.2
    fresh = Calibrator(min_rows=50)
    for k, s, y in cal.rows:
        fresh.add(k, s, bool(y))
    for i in range(300):  # outcomes that become known AFTER the query date must not change the answer
        fresh.add(date(2027, 1, 1), -5.0, True)
    assert fresh.prob(2.0, date(2025, 12, 1)) == pytest.approx(hi)


def test_allocate_respects_caps_threshold_and_core():
    cfg = MasterConfig()
    cands = [Candidate(f"S{i}", 0.75, 0.2, sector="Tech", source="bonsai") for i in range(10)]
    cands += [Candidate("W", 0.52, 0.2, sector="Energy"), Candidate("V", 0.9, 0.3, sector="Energy")]
    a = allocate(cands, {"BTC-USD": {"on": 1.0, "vol": 0.6}, "ETH-USD": {"on": 0.0, "vol": 0.8}}, cfg)
    w = a.weights
    assert "W" not in w and "below" in a.dropped["W"]
    assert all(w[f"S{i}"] <= cfg.name_cap + 1e-12 for i in range(10))
    assert sum(w[f"S{i}"] for i in range(10)) <= cfg.sector_cap + 1e-9
    assert w["BTC-USD"] == pytest.approx(cfg.crypto_cap) and "ETH-USD" not in w
    assert sum(w.values()) == pytest.approx(1 - cfg.cash_buffer) and w["SPY"] > 0
    calm = allocate(cands, {}, cfg, drawdown=0.2)
    assert calm.weights["V"] == pytest.approx(a.weights["V"] / 2)


def test_crypto_trend_rule_reads_only_past_closes():
    days = pd.bdate_range("2024-01-01", periods=300)
    up = pd.Series(np.linspace(100, 300, 300), index=days)
    close = pd.DataFrame({"BTC-USD": up, "ETH-USD": up[::-1].to_numpy()}, index=days)
    st = crypto_state(close, days[250], ("BTC-USD", "ETH-USD"))
    assert st["BTC-USD"]["on"] == 1.0 and st["ETH-USD"]["on"] == 0.0
    later = close.copy()
    later.iloc[251:] = 1.0
    assert crypto_state(later, days[250], ("BTC-USD", "ETH-USD")) == st


def test_weights_trade_at_the_next_open_with_whole_shares_and_fractional_crypto():
    days = pd.bdate_range("2025-01-06", periods=10)
    opens = pd.DataFrame({"SPY": np.full(10, 500.0), "BTC-USD": np.full(10, 60_000.0)}, index=days)
    closes = opens * 1.001
    sim = simulate_weights({days[0].date(): {"SPY": 0.5, "BTC-USD": 0.3}}, opens, closes, days[0].date(),
                           days[-1].date(), cost_bps=0)
    log = sim.weights_log[0]
    assert log["day"] == days[1].date().isoformat()  # decided day 0, traded day 1
    assert sim.trades == 2 and sim.equity[-1] == pytest.approx(100_000 * (1 + 0.8 * 0.001), rel=1e-3)
