import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import full_auto as fa


def lot(sym, rating, w, state="open"):
    return {"symbol": sym, "rating": rating, "weight": w, "state": state, "side": 1 if rating > 3 else -1,
            "horizon": "short", "decided_at": "2026-10-07T01:00:00+00:00", "session": "2026-10-07",
            "exit_session": "2026-10-14"}


def test_unstrong_halves_strong_labels_and_keeps_gross():
    lots = [lot("A", 5, 0.08), lot("B", 4, 0.04), lot("C", 1, 0.08), lot("D", 5, 0.5, state="closed")]
    out = fa.unstrong(lots)
    open_w = [x["weight"] for x in out if x["state"] == "open"]
    assert sum(open_w) == pytest.approx(0.20)  # gross of the open book unchanged
    assert open_w[0] == pytest.approx(open_w[1]) == pytest.approx(open_w[2])  # strong no longer double


def test_beta_cap_scales_stocks_when_the_hedge_is_capped():
    w = {"A": 0.6, "B": 0.6, "QQQ": -0.24, "QQQM": -0.24, "XLK": -0.24, "VGT": -0.24}
    out = fa.beta_cap(w, {"A": 1.5, "B": 1.5}, 0.30)
    net = 1.5 * (out["A"] + out["B"]) + sum(out[e] for e in ("QQQ", "QQQM", "XLK", "VGT"))
    assert net == pytest.approx(0.30) and out["QQQ"] == -0.24
    assert fa.beta_cap({"A": 0.2, "QQQ": -0.2}, {"A": 1.0}, 0.3) == {"A": 0.2, "QQQ": -0.2}


def test_participation_caps_new_risk_only():
    orders = [{"symbol": "X", "qty": 1000, "reducing": False}, {"symbol": "Y", "qty": 1000, "reducing": True},
              {"symbol": "Z", "qty": 10, "reducing": False}]
    out = fa.participation(orders, {"X": 100, "Y": 100, "Z": 100}, {"X": 1_000_000, "Y": 1_000, "Z": 50})
    assert out[0]["qty"] == 100 and out[1]["qty"] == 1000 and len(out) == 2  # Z capped below one share: dropped


def test_controls_are_seeded_and_mirror_the_lot():
    a = fa.controls(lot("A", 5, 0.05), mom=-1)
    assert [c["kind"] for c in a] == ["random", "momentum"] and a[1]["side"] == -1
    assert a == fa.controls(lot("A", 5, 0.05), mom=-1) and all(c["weight"] == 0.05 for c in a)
