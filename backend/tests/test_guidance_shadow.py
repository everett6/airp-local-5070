"""Synthetic prospective signal collection; does not evaluate market history."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import guidance_shadow as G

from app.forward.ledger import Ledger


def test_eligibility_is_frozen_and_bonsai_only():
    row = {"type": "decision", "source": "bonsai", "on_time": True, "logodds": 3.0,
           "accepted_utc": "2026-09-30T20:00:00"}
    assert G.eligible(row)
    assert not G.eligible(row | {"source": "lite"})
    assert not G.eligible(row | {"logodds": 2.0})
    assert not G.eligible(row | {"accepted_utc": "2026-09-29T20:00:00"})


def test_collect_is_idempotent_and_costed(tmp_path):
    source = tmp_path / "events"
    source.mkdir()
    l = Ledger(source / "ledger.jsonl")
    base = {"ticker": "AAA", "source": "bonsai", "on_time": True, "logodds": 3.0,
            "accepted_utc": "2026-09-30T20:00:00", "entry_deadline": "2026-10-01T09:30:00-04:00"}
    l.append("decision", accession="a", guidance="raised", **base)
    l.append("decision", accession="b", guidance="maintained", **base)
    l.append("outcome", accession="a", entry="2026-10-01", fwd5=0.02)
    l.append("outcome", accession="b", entry="2026-10-01", fwd5=0.0)
    out = tmp_path / "shadow"
    first = G.collect(source, out)
    assert first["signals"] == 2 and first["matured"] == 2 and first["raised_matured"] == 1
    assert first["raised_mean_net"] == 0.012 and first["control_mean_net"] == 0.002
    assert first["verdict"] == "collecting"
    n = len(Ledger(out / "ledger.jsonl").verify())
    assert G.collect(source, out) == first
    assert len(Ledger(out / "ledger.jsonl").verify()) == n
