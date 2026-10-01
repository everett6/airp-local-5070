"""State files of the forward test (app/forward/ledger.py)."""
from __future__ import annotations


def test_write_atomic_replaces_in_one_step(tmp_path):
    from app.forward.ledger import write_atomic
    f = tmp_path / "state.json"
    f.write_text("old")
    write_atomic(f, "new\n")
    assert f.read_text() == "new\n" and list(tmp_path.iterdir()) == [f]  # no temporary file left behind


def test_append_after_a_cut_off_line_starts_a_fresh_line(tmp_path):
    import json

    import pytest

    from app.forward.ledger import jsonl_records, open_append
    f = tmp_path / "labels.jsonl"
    with open_append(f) as w:  # a new file
        w.write(json.dumps({"accession": "a"}) + "\n")
    with f.open("a") as w:
        w.write('{"accession": "b", "quo')  # power loss mid-write
    with open_append(f) as w:
        w.write(json.dumps({"accession": "c"}) + "\n")
    assert [r["accession"] for r in jsonl_records(f)] == ["a", "c"]
    assert f.read_text().count("\n") == 3
    with open_append(f) as w:  # a whole file is appended to as usual
        w.write(json.dumps({"accession": "d"}) + "\n")
    assert f.read_text().count("\n") == 4
    assert jsonl_records(tmp_path / "none.jsonl") == []
    with pytest.raises(FileNotFoundError):
        jsonl_records(tmp_path / "none.jsonl", must_exist=True)
