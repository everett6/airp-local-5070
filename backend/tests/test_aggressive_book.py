"""The user's aggressive 2.5x paper book (docs/PLAN_60_V2.md "Aggressive book, 2.5x"): it borrows, pays interest,
follows the frozen book's brake, has its own mandate limits, and can never disturb the frozen books."""
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.portfolio.forward import AGGRESSIVE, BORROW_RATE, Book, books_from_json, new_books, step
from app.portfolio.guard import MANDATE, MandateError, gate, load_mandate
from app.portfolio.master import MasterConfig

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def frames(n: int = 220, end: str = "2026-10-16"):
    days = pd.bdate_range(end=end, periods=n)
    up = np.linspace(100, 200, n)
    opens = pd.DataFrame({"SPY": up, "BTC-USD": up * 500, "ETH-USD": up * 20, "SGOV": 100.0}, index=days)
    return opens, opens * 1.001


def run(books, opens, closes, day):
    cut = pd.Timestamp(day)
    return step(books, opens[opens.index <= cut], closes[closes.index <= cut],
                datetime(cut.year, cut.month, cut.day, 22, 0, tzinfo=UTC), MasterConfig())


def test_targets_are_two_and_a_half_times_the_frozen_book_and_pass_its_own_limits():
    opens, closes = frames()
    books = new_books()
    rec = run(books, opens, closes, "2026-10-02")
    base, agg = rec["books"]["master+brakes"]["new_targets"], rec["books"][AGGRESSIVE]["new_targets"]
    assert agg == {a: pytest.approx(2.5 * w, abs=1e-4) for a, w in base.items() if a != "SGOV"}
    assert 2.4 < sum(agg.values()) <= 2.5
    assert gate(agg, MANDATE, AGGRESSIVE) == []
    assert gate(agg, MANDATE) and gate(agg, MANDATE, "master")  # every other book keeps the 1.0x limits


def test_the_frozen_books_are_identical_with_and_without_the_aggressive_book():
    opens, closes = frames()
    with_agg, without = new_books(), {k: v for k, v in new_books().items() if k != AGGRESSIVE}
    for day in ("2026-10-02", "2026-10-09", "2026-10-16"):
        a, b = run(with_agg, opens, closes, day), run(without, opens, closes, day)
        assert {k: v for k, v in a["books"].items() if k != AGGRESSIVE} == b["books"]
    assert all(with_agg[k] == without[k] for k in without)


def test_it_borrows_within_its_limit_and_pays_interest_between_runs():
    opens, closes = frames()
    books = new_books()
    run(books, opens, closes, "2026-10-02")
    r2 = run(books, opens, closes, "2026-10-09")["books"][AGGRESSIVE]
    b = books[AGGRESSIVE]
    assert r2["filled_on"] == "2026-10-05" and 2.3 < r2["gross"] < 2.6
    assert -1.5 * r2["equity"] <= b.cash < 0 and r2["borrowed"] == pytest.approx(-b.cash, abs=0.01)
    assert b.interest == 0.0 and b.interest_through == "2026-10-09"  # nothing was borrowed before this run
    debt = -b.cash
    r3 = run(books, opens, closes, "2026-10-16")["books"][AGGRESSIVE]
    assert r3["interest_paid"] == pytest.approx(debt * BORROW_RATE * 7 / 365, abs=0.01)
    assert books["master+brakes"].interest == 0.0 and books["master+brakes"].cash >= 0


def test_it_follows_the_frozen_books_brake_not_its_own():
    opens, closes = frames()
    books = new_books()
    books["master+brakes"].peak = 115_000.0  # the frozen book is 13% below its peak: brake at 2/3
    rec = run(books, opens, closes, "2026-10-02")
    assert rec["books"][AGGRESSIVE]["brake"] == pytest.approx(2 / 3, abs=1e-3)
    assert sum(rec["books"][AGGRESSIVE]["new_targets"].values()) == pytest.approx(2.5 * 0.98 * 2 / 3, abs=0.02)


def test_a_wipe_out_closes_only_the_aggressive_book():
    import forward_allocator as fa
    opens, closes = frames()
    books = new_books()
    run(books, opens, closes, "2026-10-02")
    run(books, opens, closes, "2026-10-09")
    closes.loc["2026-10-12":] *= 0.5  # everything halves: a 2.5x book owes more than it holds
    opens.loc["2026-10-12":] *= 0.5
    rec = run(books, opens, closes, "2026-10-16")
    assert rec["books"][AGGRESSIVE] == {"equity": 0.0, "positions": {}, "wiped_out": True}
    assert books[AGGRESSIVE].wiped and books[AGGRESSIVE].pending is None
    assert rec["books"]["master+brakes"]["equity"] > 40_000
    rec["price_sources"] = {"SPY": "test"}
    fa.validate_result(rec, books)  # reported, not raised: the frozen books' run still goes through
    assert rec["aggressive_issues"] == [f"{AGGRESSIVE}: wiped_out"]
    again = run(books, opens, closes, "2026-10-16")
    assert again["books"][AGGRESSIVE]["wiped_out"] and books[AGGRESSIVE].positions == {}


def test_only_a_book_with_its_own_section_may_exceed_one_times(tmp_path):
    m = load_mandate(MANDATE, AGGRESSIVE)
    assert (m.max_gross, m.max_weight, m.max_crypto, m.max_drawdown, m.alert_drawdown) == (2.5, 2.0, 0.5, 0.6, 0.45)
    base = load_mandate(MANDATE)
    assert base == load_mandate(MANDATE, "master+brakes") and base.max_gross == 1.0 and base.max_drawdown == 0.35
    d = json.loads(MANDATE.read_text())
    d["max_gross"] = 2.0  # the general limits can never be raised above 1.0 by editing the top level
    p = tmp_path / "m.json"
    p.write_text(json.dumps(d))
    with pytest.raises(MandateError):
        load_mandate(p)


def test_old_state_without_the_new_fields_still_loads():
    old = {"master+brakes": {"name": "master+brakes", "cash": 5.0, "positions": {"SPY": 1.0}, "pending": None,
                             "decided_at": None, "trades": 1, "costs": 0.1, "peak": 10.0}}
    books = books_from_json(old)
    assert books["master+brakes"] == Book("master+brakes", 5.0, {"SPY": 1.0}, None, None, 1, 0.1, 10.0)
    assert list(books)[-1] == AGGRESSIVE and books[AGGRESSIVE].cash == 100_000.0


def test_the_broker_holds_what_its_margin_rules_allow():
    from broker_sync import REAL_BOOK, broker_scale
    assert REAL_BOOK[0] == AGGRESSIVE
    assert broker_scale({"SPY": 0.78, "BTC-USD": 0.12, "ETH-USD": 0.08}) == 1.0
    t = {"SPY": 1.95, "BTC-USD": 0.29, "ETH-USD": 0.21}
    s = broker_scale(t)
    assert s == pytest.approx(0.98 / (0.5 * 1.95 + 0.5), abs=1e-9) and 0.6 < s < 0.7
    assert 0.5 * t["SPY"] * s + (t["BTC-USD"] + t["ETH-USD"]) * s == pytest.approx(0.98)
    assert broker_scale({}) == 1.0
