from datetime import date

import pytest

from app.portfolio import auto_trader as at

CFG = at.Config()
MON = date(2026, 10, 5)


def dec(ratings, **kw):
    return {"ticker": "ACME", "decided_at": "2026-10-04T22:00:00+00:00", "ratings": ratings, **kw}


def test_sizing_rules_apply_in_order_and_explain_themselves():
    w, why = at.size("day", 2.0, False, "event", "day", None, CFG)
    assert w == pytest.approx(0.10) and why == ["base 5% x2 strong"]
    w, why = at.size("medium", 1.0, True, "quoted", "day", None, CFG)
    assert w == pytest.approx(0.04 * 0.6 * 0.5 * 0.5)
    assert why[1:] == ["x0.6 quoted support", "x0.5 not the primary horizon", "x0.5 against the market"]
    w, why = at.size("long", 2.0, False, "event", None, 0.04, CFG)  # volatile stock, 63-session hold
    assert w == pytest.approx(0.01 / (1.5 * 0.04 * 63 ** 0.5)) and why[-1].startswith("risk cap")
    assert at.size("day", 2.0, False, "none", None, None, CFG)[0] == pytest.approx(0.03)


def test_calls_become_long_and_short_lots_with_their_holding_periods():
    lots = at.new_lots(dec({"day": 4, "short": 2, "medium": 5, "long": 3}, primary="medium"), MON, "bull", CFG, [])
    by = {x["horizon"]: x for x in lots}
    assert set(by) == {"day", "short", "medium"}
    assert by["day"]["side"] == 1 and by["short"]["side"] == -1 and by["medium"]["side"] == 1
    assert by["day"]["exit_session"] == "2026-10-05" and by["short"]["exit_session"] == "2026-10-12"
    assert by["medium"]["weight"] == pytest.approx(0.08) and by["short"]["weight"] == pytest.approx(0.04 * 0.5 * 0.5)
    assert at.new_lots(dec({"day": 4}), MON, "bull", CFG, lots) == []  # same decision twice: nothing new
    assert at.new_lots(dec({"day": 4}, day_feasible=False), MON, "bull", CFG, []) == []


def test_weights_caps_and_orders():
    lots = [{"symbol": "A", "side": 1, "weight": 0.08, "state": "open"},
            {"symbol": "A", "side": 1, "weight": 0.08, "state": "open"},
            {"symbol": "B", "side": -1, "weight": 0.05, "state": "open"},
            {"symbol": "C", "side": 1, "weight": 0.05, "state": "closed"}]
    w = at.weights(lots, CFG)
    assert w == {"A": pytest.approx(0.10), "B": pytest.approx(-0.05)}
    t = at.target_shares(w, 100_000, {"A": 100.0, "B": 50.0})
    assert t == {"A": 100, "B": -100}
    ords = at.orders(t, {"A": 40, "C": 10, "B": 20}, {"A": 100.0, "B": 50.0, "C": 10.0}, CFG)
    assert ords[0]["reducing"] and {o["symbol"]: (o["side"], o["qty"]) for o in ords} == \
        {"A": ("buy", 60), "B": ("sell", 20), "C": ("sell", 10)}  # B flips: closed first, opened later


def test_day_lots_close_by_the_close_swing_lots_on_their_exit_day_and_stops():
    lots = at.new_lots(dec({"day": 4, "short": 4}), MON, "bull", CFG, [])
    assert at.expire(lots, MON, flatten_day=False) == []
    assert [x["horizon"] for x in at.expire(lots, MON, flatten_day=True)] == ["day"]
    assert [x["horizon"] for x in at.expire(lots, date(2026, 10, 12), False)] == ["short"]
    lot = {"symbol": "A", "horizon": "short", "side": -1, "state": "open", "entry_price": 100.0}
    assert at.stop_out([lot], {"A": 109.0}, CFG) == [] and len(at.stop_out([lot], {"A": 110.5}, CFG)) == 1


def test_regime_and_drawdown():
    assert at.regime([1.0] * 49) == "unknown"
    assert at.regime([1.0] * 49 + [2.0]) == "bull" and at.regime([2.0] * 49 + [1.0]) == "bear"
    assert at.drawdown_hit(65_000, 100_000, CFG) and not at.drawdown_hit(66_000, 100_000, CFG)
    with pytest.raises(ValueError):
        at.Config(risk_per_trade=0.2)


def test_a_themed_stock_trades_only_on_its_themes_horizons():
    ai = at.new_lots(dec({"day": 4, "short": 5, "medium": 4, "long": 5}, theme_horizons=["short", "medium"],
                         theme="ai_tech"), MON, "bull", CFG, [])
    assert sorted(x["horizon"] for x in ai) == ["medium", "short"] and ai[0]["theme"] == "ai_tech"
    nuclear = at.new_lots(dec({"day": 2, "short": 4, "medium": 4, "long": 5}, theme_horizons=["long"]), MON, "bull", CFG, [])
    assert [x["horizon"] for x in nuclear] == ["long"]
    plain = at.new_lots(dec({"day": 4, "short": 4, "medium": 4, "long": 4}), MON, "bull", CFG, [])
    assert len(plain) == 4  # no theme: every horizon


def test_the_autopilot_leaves_research_to_the_150_company_run_while_it_works(tmp_path):
    import sys
    from datetime import UTC, datetime, timedelta
    sys.path.insert(0, "scripts")
    from full_auto import research_all_active
    p, now = tmp_path / "progress.json", datetime(2026, 10, 5, 3, tzinfo=UTC)
    assert not research_all_active(now, p)  # no run
    p.write_text(f'{{"state": "running", "updated_at": "{(now - timedelta(minutes=20)).isoformat()}"}}')
    assert research_all_active(now, p)
    p.write_text(f'{{"state": "finished", "updated_at": "{now.isoformat()}"}}')
    assert not research_all_active(now, p)
    p.write_text(f'{{"state": "running", "updated_at": "{(now - timedelta(hours=4)).isoformat()}"}}')
    assert not research_all_active(now, p)  # a stale file from a run that died
