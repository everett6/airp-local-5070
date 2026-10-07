import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import algo_engine as ae


def test_share_orders_reduce_first_and_close_flips():
    o = ae.share_orders({"SPY": 0.5, "XLF": -0.2}, {"SPY": 10, "XLF": 30, "XLE": 5}, 10_000,
                        {"SPY": 500, "XLF": 50, "XLE": 90})
    by = {x["symbol"]: x for x in o}
    assert by["XLF"] == {"symbol": "XLF", "side": "sell", "qty": 30, "to": 0.0}  # flip: close first
    assert by["XLE"]["side"] == "sell" and by["XLE"]["to"] == 0
    assert "SPY" not in by  # already at target (0.5 x 10k / 500 = 10 shares)
    assert [x["symbol"] for x in o] == ["XLE", "XLF"]


def test_shadow_step_charges_costs_and_marks_to_market():
    book = {"equity": 1.0, "w": {}, "px": {}}
    r0 = ae.shadow_step(book, {"XLF": 0.2}, {"XLF": 50.0})
    assert abs(r0 + 0.2 * 1.5e-4) < 1e-12
    r1 = ae.shadow_step(book, {}, {"XLF": 51.0})
    assert abs(r1 - (0.2 * 0.02 - 0.2 * 1.5e-4)) < 1e-12
    assert book["w"] == {} and abs(book["equity"] - (1 + r0) * (1 + r1)) < 1e-12


def test_window_and_breaker_rules():
    cfg = {**ae.DEFAULTS, "start": "10:00", "end": "11:00"}
    inside = [m for m in ae.me.DECISION_BARS if ae.in_window(m, cfg)]
    assert inside[0] == 29 and inside[-1] == 84 and len(inside) == 12  # 10:00 .. 10:55 decisions
    got = ae.no_new_risk({"SPY": 0.3, "XLF": -0.2, "XLE": 0.1}, {"SPY": 0.2, "XLF": 0.2, "IWM": 0.1})
    assert got == {"SPY": 0.2}  # keep or shrink only; no flip, nothing new


def test_config_falls_back_to_defaults(tmp_path, monkeypatch):
    p = tmp_path / "day.json"
    p.write_text('{"start": "10:30", "gross": 2.5, "unknown": 1}')
    monkeypatch.setattr(ae, "CONFIG", p)
    c = ae.config()
    assert c["start"] == "10:30" and c["end"] == "15:55" and c["gross"] == 2.5 and "unknown" not in c


# --- review #24: Day's live path rehearsed against the fake broker (final broker state asserted) ---------------
def engine(tmp_path, broker):
    from test_account_book import SESSION

    from app.portfolio import account_book as ab
    e = object.__new__(ae.Engine)
    e.alpaca, e.ab, e.session = broker, ab.Book(tmp_path), SESSION
    e.breaker = e.stopped_today = e.flat_done = e.stop = False
    e.op, e.done, e.status, e.stop_file = "trading", set(), {}, None
    e.book = {"equity": 1.0, "w": {}, "px": {}}
    e.last_bar_wall = ae.time.monotonic()
    return e


def test_live_trade_reduces_first_then_adds_from_actual_fills(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).parent))
    from test_account_book import FakeBroker
    monkeypatch.setattr(ae, "ALGO", tmp_path)
    br = FakeBroker({"XLF": 100, "AAPL": 50})
    e = engine(tmp_path, br)
    cfg = {**ae.DEFAULTS}
    out = e.trade_sync({"XLE": 0.2, "SPY": -0.2}, {"XLF": 100, "XLE": 100, "SPY": 100}, 100_000, cfg, 29, False)
    assert br.pos["XLF"] == 0 and br.pos["XLE"] == 200 and br.pos["SPY"] == -200 and br.pos["AAPL"] == 50
    assert all(o["status"] == "filled" for o in out) and all(o["id"].startswith("dy-") for o in out)
    assert all(o["limit"] for o in out)  # bounded limit orders


def test_breaker_uses_broker_holdings_and_closing_flattens_verified(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).parent))
    from test_account_book import FakeBroker
    monkeypatch.setattr(ae, "ALGO", tmp_path)
    br = FakeBroker({"XLE": 200})
    e = engine(tmp_path, br)
    e.breaker = True
    e.book["w"] = {"XLB": 0.3}  # the shadow book disagrees with the broker; the broker wins
    e.trade_sync({"XLE": 0.5, "XLB": 0.3}, {"XLE": 100, "XLB": 100}, 100_000, {**ae.DEFAULTS}, 34, False)
    assert br.pos["XLE"] == 200 and br.pos.get("XLB", 0) == 0  # nothing new, nothing grown
    out = e.trade_sync({}, {"XLE": 100}, 100_000, {**ae.DEFAULTS}, 380, True)
    assert out[0]["flatten"]["flat"] and br.pos["XLE"] == 0 and e.flat_done


def test_clock_guard_flattens_at_the_deadline_without_bars(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).parent))
    from test_account_book import FakeBroker
    monkeypatch.setattr(ae, "ALGO", tmp_path)
    (tmp_path / "LIVE").touch()
    br = FakeBroker({"SPY": 10})
    e = engine(tmp_path, br)
    e.session = date(2026, 10, 7)
    e.last_bar_wall = ae.time.monotonic()  # data is fine; only the clock says stop
    monkeypatch.setattr(ae, "now_utc", lambda: ae.datetime(2026, 10, 7, 19, 56, tzinfo=ae.UTC))  # 15:56 New York
    monkeypatch.setattr(ae, "config", lambda: {**ae.DEFAULTS})
    real_sleep = ae.asyncio.sleep

    async def fast(_s):
        e.stop = e.stop or e.flat_done
        await real_sleep(0)

    monkeypatch.setattr(ae.asyncio, "sleep", fast)
    ae.asyncio.run(e.guard())
    assert br.pos["SPY"] == 0 and e.flat_done
    assert ae.json.loads((tmp_path / "day_state.json").read_text())["flat_done"]


def test_latches_survive_a_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(ae, "ALGO", tmp_path)
    e = engine(tmp_path, None)
    e.session, e.breaker, e.done = date(2026, 10, 7), True, {29, 34}
    e.save_latches()
    f = engine(tmp_path, None)
    f.load_latches(date(2026, 10, 7))
    assert f.breaker and f.done == {29, 34}
    g = engine(tmp_path, None)
    g.load_latches(date(2026, 10, 8))
    assert not g.breaker
