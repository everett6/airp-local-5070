"""Synthetic pair reconciliation: no Alpaca calls."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ai_picks import audit_pair

from app.portfolio.broker import Leg


def pair(status="open"):
    return {"status": status, "ticker": "AAA", "etf": "XLK", "qty": 10, "etf_qty": 20,
            "entry_open": 100.0, "etf_entry_open": 50.0, "exit_open": 110.0,
            "etf_exit_open": 51.0, "pnl": 72.0}


def leg(phase, asset, status="filled", price=100.0):
    return Leg(asset, asset, "buy", 10, "opg", f"airp-pk-a-{phase}-{asset}", price,
               status=status, filled_price=price if status == "filled" else None)


def test_one_sided_entry_alerts_once_then_clears():
    p = pair()
    only_stock = [leg("in", "AAA")]
    assert "missing XLK" in audit_pair(p, only_stock)[0]
    assert audit_pair(p, only_stock) == []
    both = only_stock + [leg("in", "XLK", "rejected")]
    assert any("rejected/canceled" in a for a in audit_pair(p, both))
    assert audit_pair(p, [leg("in", "AAA"), leg("in", "XLK", price=50.0)]) == []
    assert p["audit_flags"] == []


def test_closed_pair_records_fill_slippage_and_assumed_cost():
    p = pair("closed")
    legs = [leg("in", "AAA", price=101.0), leg("in", "XLK", price=50.0),
            leg("out", "AAA", price=110.0), leg("out", "XLK", price=51.0)]
    assert audit_pair(p, legs) == []
    a = p["broker_audit"]
    assert a["broker_gross_pnl"] == 70.0
    assert a["simulator_net_pnl"] == 72.0
    assert a["assumed_sim_cost"] == 8.24
    assert a["fill_slippage"] == -10.24
