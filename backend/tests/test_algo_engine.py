import sys
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
