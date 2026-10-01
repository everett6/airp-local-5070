"""State files of the forward test (app/forward/ledger.py)."""
from __future__ import annotations


def test_write_atomic_replaces_in_one_step(tmp_path):
    from app.forward.ledger import write_atomic
    f = tmp_path / "state.json"
    f.write_text("old")
    write_atomic(f, "new\n")
    assert f.read_text() == "new\n" and list(tmp_path.iterdir()) == [f]  # no temporary file left behind
