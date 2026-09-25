"""Paper-trading simulator: no lookahead, rejected bad signals, balanced books, risk halt, ties, costs, beta."""
from datetime import date, timedelta

import numpy as np
import pytest

from app.portfolio.simulator import SimConfig, beta_alpha, buy_and_hold, excess_vs, simulate


def _bars(n_days=120, tickers=("A", "B", "C", "D", "SPY"), drift=None, seed=0):
    """Daily OHLCV bars on weekdays; open = previous close x small gap, close = open x daily move."""
    rng = np.random.default_rng(seed)
    drift = drift or {}
    days = []
    d = date(2025, 1, 6)
    while len(days) < n_days:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    bars = {}
    for t in tickers:
        c, rows = 100.0, []
        for day in days:
            o = c * (1 + rng.normal(0, 0.002))
            c = o * (1 + drift.get(t, 0.0) + rng.normal(0, 0.01))
            rows.append((day, o, max(o, c), min(o, c), c, 1e6))
        bars[t] = rows
    return bars, days


def _weekly(days, scores):
    """Signals every 5th day; `scores` maps ticker -> p (or a function of the day index)."""
    out = []
    for i in range(0, len(days) - 1, 5):
        for t, s in scores.items():
            out.append({"cutoff": days[i], "ticker": t, "p": s(i) if callable(s) else s})
    return out


def test_perfect_picks_beat_holding_everything():
    bars, days = _bars(drift={"A": 0.004, "B": -0.004, "C": -0.004, "D": -0.004})
    good = simulate(_weekly(days, {"A": 0.9, "B": 0.1, "C": 0.1, "D": 0.1}), bars,
                    SimConfig(top_k=1, max_weight=0.98), universe={"A", "B", "C", "D"})
    bad = simulate(_weekly(days, {"A": 0.1, "B": 0.9, "C": 0.1, "D": 0.1}), bars,
                   SimConfig(top_k=1, max_weight=0.98), universe={"A", "B", "C", "D"})
    assert good.equity[-1] > 100_000 > bad.equity[-1]


def test_trades_execute_at_the_next_open_never_on_the_signal_day():
    bars, days = _bars()
    sim = simulate(_weekly(days, {"A": 0.9, "B": 0.1}), bars, SimConfig(top_k=1), universe={"A", "B"})
    signal_days = {days[i] for i in range(0, len(days) - 1, 5)}
    assert sim.trades and all(t.day not in signal_days for t in sim.trades)
    first = sim.trades[0]
    assert first.day == days[1]
    assert first.price == pytest.approx(bars["A"][1][1] * (1 + 5 / 1e4))  # day-1 open plus slippage


def test_a_signal_on_the_last_day_is_never_executed():
    bars, days = _bars(n_days=20)
    sim = simulate([{"cutoff": days[0], "ticker": "A", "p": 0.9},
                    {"cutoff": days[-1], "ticker": "B", "p": 0.9}], bars, SimConfig(top_k=1), universe={"A", "B"})
    assert {t.ticker for t in sim.trades} == {"A"}


def test_bad_model_outputs_are_rejected_and_counted_not_traded():
    bars, days = _bars(n_days=20)
    sigs = [{"cutoff": days[0], "ticker": "A", "p": 0.9},
            {"cutoff": days[0], "ticker": "B", "p": 1.7},              # impossible probability
            {"cutoff": days[0], "ticker": "C", "p": float("nan")},
            {"cutoff": days[0], "ticker": "ZZZ", "p": 0.99},          # stock that does not exist
            {"cutoff": date(2025, 1, 4), "ticker": "D", "p": 0.99},   # a Saturday
            {"cutoff": days[0], "ticker": "D"},                       # no probability at all
            {"cutoff": "not a date", "ticker": "D", "p": 0.5}]
    sim = simulate(sigs, bars, SimConfig(top_k=4), universe={"A", "B", "C", "D"})
    assert {t.ticker for t in sim.trades} == {"A"}
    assert sim.rejected == {"probability_out_of_range": 2, "unknown_ticker": 1, "not_a_trading_day": 1, "malformed": 2}
    assert sim.metrics()["rejected_signals"] == sim.rejected


def test_no_usable_signals_is_an_error_not_a_silent_flat_run():
    bars, days = _bars(n_days=20)
    with pytest.raises(ValueError, match="no usable signals"):
        simulate([{"cutoff": days[0], "ticker": "A", "p": 3.0}], bars, universe={"A"})


def test_books_balance_whole_shares_caps_and_cash():
    bars, days = _bars(seed=3)
    sim = simulate(_weekly(days, {t: (lambda i, t=t: 0.5 + 0.1 * ((i // 5 + ord(t)) % 3)) for t in "ABCD"}),
                   bars, SimConfig(top_k=4, max_weight=0.2, cash_buffer=0.02), universe=set("ABCD"))
    assert all(isinstance(t.shares, int) for t in sim.trades)
    # rebuild the books from the trade list and check every day's equity
    cash, pos, k = 100_000.0, {}, 0
    for d, eq in zip(sim.days, sim.equity, strict=True):
        while k < len(sim.trades) and sim.trades[k].day == d:
            t = sim.trades[k]
            cash -= t.shares * t.price
            pos[t.ticker] = pos.get(t.ticker, 0) + t.shares
            k += 1
        closes = {tk: next(r[4] for r in bars[tk] if r[0] == d) for tk in pos}
        assert cash >= -1e-6
        assert eq == pytest.approx(cash + sum(q * closes[tk] for tk, q in pos.items()), rel=1e-9)
    # no single stock ever above its cap by more than one day's move
    assert max(q * next(r[4] for r in bars[tk] if r[0] == sim.days[-1]) for tk, q in pos.items() if q) \
        < 0.2 * sim.equity[-1] * 1.25


def test_drawdown_halt_sells_everything_and_stops_trading():
    bars, days = _bars(drift={"A": -0.02})
    sim = simulate(_weekly(days, {"A": 0.9}), bars, SimConfig(top_k=1, max_weight=0.98, drawdown_halt=0.10),
                   universe={"A"})
    assert sim.halted_on is not None
    halt_trades = [t for t in sim.trades if t.reason == "halt"]
    assert halt_trades and halt_trades[0].day > sim.halted_on
    assert not [t for t in sim.trades if t.day > halt_trades[0].day]  # nothing after the halt
    assert min(sim.equity) > 100_000 * 0.80  # stopped out near the limit, not ridden to the bottom


def test_ties_are_broken_randomly_and_reported():
    bars, days = _bars(tickers=("A", "B", "C", "D", "E", "F", "SPY"))
    tied = _weekly(days, dict.fromkeys("ABCDEF", 0.52))
    picks = {frozenset(t.ticker for t in simulate(tied, bars, SimConfig(top_k=2, tie_seed=k), universe=set("ABCDEF"))
                       .trades if t.day == days[1]) for k in range(12)}
    assert len(picks) > 1  # different seeds pick different stocks: the choice was never the model's
    one = simulate(tied, bars, SimConfig(top_k=2), universe=set("ABCDEF"))
    assert one.metrics()["tie_decided_pct"] == 100.0
    clear = simulate(_weekly(days, {"A": 0.9, "B": 0.8, "C": 0.1, "D": 0.1, "E": 0.1, "F": 0.1}), bars,
                     SimConfig(top_k=2), universe=set("ABCDEF"))
    assert clear.metrics()["tie_decided_pct"] == 0.0


def test_trading_costs_reduce_returns():
    bars, days = _bars(seed=5)
    flip = _weekly(days, {"A": lambda i: 0.9 if (i // 5) % 2 else 0.1, "B": lambda i: 0.1 if (i // 5) % 2 else 0.9})
    free = simulate(flip, bars, SimConfig(top_k=1, slippage_bps=0.0), universe={"A", "B"})
    costly = simulate(flip, bars, SimConfig(top_k=1, slippage_bps=50.0), universe={"A", "B"})
    assert costly.equity[-1] < free.equity[-1] and costly.costs_paid > 0 == free.costs_paid


def test_beta_alpha_sees_through_a_levered_market_bet():
    bars, days = _bars(n_days=250, seed=7)
    spy = buy_and_hold(bars, "SPY", days[0])
    rets = np.diff(spy.equity) / np.asarray(spy.equity[:-1])
    lev = spy.__class__(name="lev", config=spy.config, days=spy.days,
                        equity=list(100_000 * np.concatenate([[1.0], np.cumprod(1 + 2 * rets)])))
    ba = beta_alpha(lev, spy)
    assert ba["beta"] == pytest.approx(2.0, abs=1e-6) and abs(ba["alpha_ann_pct"]) < 1e-6
    assert excess_vs(spy, spy)["excess_ann_pct"] == 0.0


def test_buy_and_hold_uses_whole_shares_at_the_first_open():
    bars, days = _bars(n_days=30)
    bh = buy_and_hold(bars, "SPY", days[0], initial_cash=10_000, slippage_bps=0)
    q = int(10_000 // bars["SPY"][0][1])
    assert bh.trades[0].shares == q
    assert bh.equity[-1] == pytest.approx(10_000 - q * bars["SPY"][0][1] + q * bars["SPY"][-1][4])
