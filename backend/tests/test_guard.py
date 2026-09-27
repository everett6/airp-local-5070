"""Mandate gate (fail closed) and kill switch for the forward books."""
import json
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from app.portfolio.forward import new_books, step
from app.portfolio.guard import (
    MANDATE,
    check,
    gate,
    halt,
    halt_info,
    halted,
    load_mandate,
    reduce_only,
    state,
)
from app.portfolio.master import MasterConfig

M = load_mandate()


def frames(n: int = 200, end: str = "2026-09-25") -> tuple[pd.DataFrame, pd.DataFrame]:
    days = pd.bdate_range(end=end, periods=n)
    up = np.linspace(100, 200, n)
    opens = pd.DataFrame({"SPY": up, "BTC-USD": up * 500, "ETH-USD": up * 20}, index=days)
    return opens, opens * 1.001


def test_frozen_book_fits_the_committed_mandate():
    assert check({"SPY": 0.78, "BTC-USD": 0.1159, "ETH-USD": 0.0841}, M) == []
    assert check({"SPY": 0.98}, M) == [] and check({"SPY": 0.78, "BTC-USD": 0.20}, M) == []


def test_breaches_and_bad_numbers_are_rejected():
    assert any("universe" in b for b in check({"TSLA": 0.1}, M))
    assert any("crypto share" in b for b in check({"SPY": 0.6, "BTC-USD": 0.3}, M))
    assert any("gross" in b for b in check({"SPY": 0.98, "BTC-USD": 0.1}, M))
    assert any("short" in b for b in check({"SPY": -0.1}, M))
    for bad in (True, "0.5", float("nan"), float("inf")):
        assert any("finite number" in b for b in check({"SPY": bad}, M))


def test_unreadable_or_invalid_mandate_fails_closed(tmp_path):
    assert gate({"SPY": 0.5}, tmp_path / "missing.json")
    p = tmp_path / "m.json"
    d = json.loads(MANDATE.read_text())
    p.write_text(json.dumps(d | {"max_gross": "1.0"}))  # a stringified number is not a number
    assert "max_gross" in gate({"SPY": 0.5}, p)[0]
    p.write_text(json.dumps(d | {"max_crypto": True}))
    assert gate({"SPY": 0.5}, p)


def test_step_rejects_targets_outside_the_mandate(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps(json.loads(MANDATE.read_text()) | {"max_crypto": 0.05}))
    opens, closes = frames()
    books = new_books()
    rec = step(books, opens, closes, datetime(2026, 9, 25, 15, 0, tzinfo=UTC), MasterConfig(), mandate=p)
    assert "crypto share" in rec["books"]["master"]["rejected"][0] and books["master"].pending is None
    assert books["SPY"].pending == {"SPY": 0.98}  # a book inside the mandate is unaffected


def test_pending_orders_are_regated_before_they_fill(tmp_path):
    opens, closes = frames(end="2026-10-02")
    books = new_books()
    step(books, opens[opens.index <= "2026-09-25"], closes[closes.index <= "2026-09-25"],
         datetime(2026, 9, 25, 15, 0, tzinfo=UTC), MasterConfig())
    tight = tmp_path / "m.json"
    tight.write_text(json.dumps(json.loads(MANDATE.read_text()) | {"universe": ["BTC-USD"], "crypto": ["BTC-USD"]}))
    rec = step(books, opens, closes, datetime(2026, 10, 2, 15, 0, tzinfo=UTC), MasterConfig(), mandate=tight)
    assert rec["books"]["SPY"]["rejected_at_fill"] and books["SPY"].positions == {}
    assert "fills" not in rec["books"]["SPY"]


def test_kill_switch_marks_but_never_fills_or_decides(tmp_path):
    opens, closes = frames(end="2026-10-02")
    books = new_books()
    step(books, opens[opens.index <= "2026-09-25"], closes[closes.index <= "2026-09-25"],
         datetime(2026, 9, 25, 15, 0, tzinfo=UTC), MasterConfig())
    before = {k: dict(b.pending or {}) for k, b in books.items()}
    rec = step(books, opens, closes, datetime(2026, 10, 2, 15, 0, tzinfo=UTC), MasterConfig(), halted=True)
    assert rec["halted"] and all(r.get("held_by_kill_switch") and "fills" not in r for r in rec["books"].values())
    assert {k: b.pending for k, b in books.items()} == before and all(not b.positions for b in books.values())
    h = tmp_path / "HALT"
    assert not halted(h)
    halt("test", by="pytest", path=h)
    halt("second", by="pytest", path=h)  # idempotent: the first record is kept
    assert halted(h) and (halt_info(h) or {})["reason"] == "test"


def test_reducing_only_ever_sells(tmp_path):
    opens, closes = frames(end="2026-10-09")
    books = new_books()
    step(books, opens[opens.index <= "2026-09-25"], closes[closes.index <= "2026-09-25"],
         datetime(2026, 9, 25, 15, 0, tzinfo=UTC), MasterConfig())
    step(books, opens[opens.index <= "2026-10-02"], closes[closes.index <= "2026-10-02"],
         datetime(2026, 10, 2, 15, 0, tzinfo=UTC), MasterConfig())
    held = {k: dict(b.positions) for k, b in books.items()}
    assert held["master"]
    books["master"].pending = {"SPY": 0.98}  # a pending order that would buy more SPY and sell the crypto
    rec = step(books, opens, closes, datetime(2026, 10, 9, 15, 0, tzinfo=UTC), MasterConfig(), reducing=True)
    assert rec["reducing"]
    for k, b in books.items():
        assert set(b.positions) <= set(held[k])  # nothing new bought
        assert all(b.positions[a] <= held[k][a] + 1e-9 for a in b.positions)  # nothing added to
    assert books["master"].positions["SPY"] == held["master"]["SPY"]  # the SPY buy was blocked


def test_trading_states_and_precedence(tmp_path):
    h = tmp_path / "HALT"
    assert state(h) == "ACTIVE"
    halt("wobbly", by="pytest", path=h, mode="REDUCING")
    assert state(h) == "REDUCING" and not halted(h)
    halt("stop", by="pytest", path=h)  # HALTED replaces REDUCING
    assert state(h) == "HALTED"
    halt("calmer", by="pytest", path=h, mode="REDUCING")  # never the other way round
    assert state(h) == "HALTED"
    h.write_text("{not json")
    assert state(h) == "HALTED"  # unreadable: fail closed
    assert reduce_only({"SPY": 0.9, "BTC-USD": 0.2}, {"SPY": 10.0}, 1000.0, {"SPY": 100.0}) == {"SPY": 0.5,
                                                                                                 "BTC-USD": 0.0}
