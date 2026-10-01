import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import ai_picks

from app.portfolio import sleeve

ETF = {"Industrials": "XLI"}
DAYS = pd.bdate_range("2026-10-01", periods=12)


def frames(n: int, stock=None, etf=None):
    s = stock or [100.0] * 12
    e = etf or [50.0] * 12
    df = pd.DataFrame({"AAA": s, "XLI": e, "SPY": [600.0] * 12}, index=DAYS)
    return df.iloc[:n], df.iloc[:n]


def dec(acc="0000000001-26-000001", lo=3.5, deadline="2026-10-05T09:30:00-04:00", source="bonsai"):
    return {"type": "decision", "accession": acc, "ticker": "AAA", "sector": "Industrials", "logodds": lo,
            "source": source, "entry_deadline": deadline}


def test_pick_threshold_source_and_late():
    st = sleeve.new_state()
    o, c = frames(2)  # bars through Fri 2 Oct
    now = datetime(2026, 10, 5, 12, 45, tzinfo=UTC)  # 08:45 ET Monday
    notes = sleeve.step(st, [{"type": "run", "as_of": "x"}, dec(), dec("x2", lo=1.0), dec("x3", source="lite"),
                             dec("x4", deadline="2026-10-02T09:30:00-04:00")], o, c, ETF, now)
    by = {p["accession"]: p for p in st["pairs"]}
    assert by["0000000001-26-000001"]["status"] == "planned"
    assert by["0000000001-26-000001"]["qty"] == 20 and by["0000000001-26-000001"]["etf_qty"] == 40
    assert "x2" not in by and "x3" not in by  # below threshold / not Bonsai: not picks at all
    assert by["x4"]["status"] == "skipped" and "after the entry open" in by["x4"]["note"]
    assert len(notes) == 2 and set(st["seen"]) == {"0000000001-26-000001", "x2", "x3", "x4"}


def test_full_round_trip_is_the_hedged_excess_after_costs():
    st = sleeve.new_state()
    now = datetime(2026, 10, 5, 12, 45, tzinfo=UTC)
    sleeve.step(st, [dec()], *frames(2), ETF, now)
    stock = [100.0] * 2 + [100.0, 101, 102, 103, 104, 110, 110, 110, 110, 110]  # entry open 100 (Mon 5 Oct)
    etf = [50.0] * 7 + [51.0] * 5                                                  # exit open: day index 2+5=7
    o, c = frames(8, stock, etf)
    sleeve.step(st, [dec()], o, c, ETF, now)
    p = st["pairs"][0]
    assert p["status"] == "closed" and p["exit_day"] == DAYS[7].date().isoformat()
    long = 20 * (110 * 0.998 - 100 * 1.002)
    short = -40 * (51 * 1.002 - 50 * 0.998)
    assert abs(p["pnl"] - round(long + short, 2)) < 0.01
    assert abs(st["equity"] - (10_000 + long + short)) < 0.01
    assert sleeve.summary(st)["closed"] == 1


def test_slots_and_drawdown_brake():
    st = sleeve.new_state()
    now = datetime(2026, 10, 5, 12, 45, tzinfo=UTC)
    ds = [dec(f"a{i}") for i in range(7)]
    sleeve.step(st, ds, *frames(2), ETF, now)
    assert [p["status"] for p in st["pairs"]].count("planned") == 5
    assert all("slots" in p["note"] for p in st["pairs"] if p["status"] == "skipped")
    st2 = sleeve.new_state()
    st2["peak"] = 20_000.0  # equity 10,000: a 50% drawdown
    sleeve.step(st2, [dec()], *frames(2), ETF, now)
    assert st2["pairs"][0]["status"] == "skipped" and "drawdown" in st2["pairs"][0]["note"]


def test_holiday_entry_fills_next_open_and_halt_blocks():
    st = sleeve.new_state()
    sleeve.step(st, [dec(deadline="2026-10-03T09:30:00-04:00")], *frames(2),  # a Saturday: no bar that day
                ETF, datetime(2026, 10, 2, 23, 0, tzinfo=UTC))
    o, c = frames(3)
    sleeve.step(st, [], o, c, ETF, datetime(2026, 10, 6, 12, 45, tzinfo=UTC))
    assert st["pairs"][0]["entry_index_day"] == "2026-10-05"
    st2 = sleeve.new_state()
    sleeve.step(st2, [dec()], *frames(2), ETF, datetime(2026, 10, 5, 12, 45, tzinfo=UTC), mode="HALTED")
    assert st2["pairs"][0]["note"] == "kill switch"


def test_broker_legs_timing():
    st = sleeve.new_state()
    morning = datetime(2026, 10, 5, 12, 45, tzinfo=UTC)
    sleeve.step(st, [dec()], *frames(2), ETF, morning)
    p = st["pairs"][0]
    legs = ai_picks.due_legs(p, DAYS[:2], morning)
    assert [(x.side, x.symbol, x.qty, x.tif) for x in legs] == [("buy", "AAA", 20, "day"), ("sell", "XLI", 40, "day")]
    assert ai_picks.due_legs(p, DAYS[:2], datetime(2026, 10, 5, 15, 0, tzinfo=UTC)) == []  # 11:00 ET: opg closed
    p["legs"] = [{"client_order_id": x.client_order_id, "asset": x.asset, "status": "filled"} for x in legs]
    assert ai_picks.due_legs(p, DAYS[:2], morning) == []  # sent already
    o, c = frames(3)
    sleeve.step(st, [], o, c, ETF, morning)
    assert p["status"] == "open"
    assert ai_picks.due_legs(p, DAYS[:6], datetime(2026, 10, 9, 12, 45, tzinfo=UTC)) == []  # bars through day 5
    out = ai_picks.due_legs(p, DAYS[:7], datetime(2026, 10, 12, 12, 45, tzinfo=UTC))  # bars through entry+4
    assert [(x.side, x.symbol) for x in out] == [("sell", "AAA"), ("buy", "XLI")]


def test_no_exit_for_an_entry_the_broker_never_filled():
    """30 Sep 2026: both opg entry legs of the first pick expired. Its exit must not be sent (it would open a short
    and buy back another pair's hedge), and closing it in the simulator raises no new alert."""
    st = sleeve.new_state()
    morning = datetime(2026, 10, 5, 12, 45, tzinfo=UTC)
    sleeve.step(st, [dec()], *frames(2), ETF, morning)
    p = st["pairs"][0]
    legs = ai_picks.due_legs(p, DAYS[:2], morning)
    for x in legs:
        x.status = "expired"
    p["legs"] = [ai_picks.leg_dict(x) for x in legs]
    sleeve.step(st, [], *frames(3), ETF, morning)
    assert p["status"] == "open"
    late = datetime(2026, 10, 12, 12, 45, tzinfo=UTC)
    assert ai_picks.due_legs(p, DAYS[:7], late) == []
    first = ai_picks.audit_pair(p, legs)
    assert any("rejected/canceled" in a for a in first)
    sleeve.step(st, [], *frames(8), ETF, late)
    assert p["status"] == "closed"
    assert ai_picks.audit_pair(p, legs) == []  # nothing new: the broken entry was alerted once
    # one leg filled, the other expired: only the filled one is closed
    p2 = {**p, "status": "open", "legs": [{**ai_picks.leg_dict(legs[0]), "status": "filled"},
                                          ai_picks.leg_dict(legs[1])]}
    out = ai_picks.due_legs(p2, DAYS[:7], late)
    assert [(x.side, x.symbol) for x in out] == [("sell", "AAA")]
