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


def test_hash_chained_ledger_survives_a_cut_off_line_and_still_catches_tampering(tmp_path):
    """A power cut during a write leaves half a line. That line was never a record: reading skips it and the next
    record starts on its own line. Tamper evidence is unchanged: removing or damaging a WHOLE record still fails."""
    import json

    import pytest

    from app.forward.ledger import Ledger, LedgerError
    led = Ledger(tmp_path / "ledger.jsonl")
    led.append("decision", accession="a")
    led.append("decision", accession="b")
    whole = led.path.read_text()
    led.path.write_text(whole + '{"accession": "c", "seq": 2, "ty')  # the third write was cut off
    assert [r["accession"] for r in led.verify()] == ["a", "b"]
    led.append("decision", accession="c")
    recs = led.verify()
    assert [(r["seq"], r["accession"]) for r in recs] == [(0, "a"), (1, "b"), (2, "c")] and recs[2]["prev"] == recs[1]["hash"]
    led.append("run")
    lines = led.path.read_text().splitlines()
    assert len(lines) == 5 and len(led.verify()) == 4  # the torn line stays in the file, as evidence
    # a whole record removed, damaged into nonsense, or edited: still caught
    for bad in ([lines[0], *lines[2:]], [lines[0], lines[1][:40], *lines[2:]],
                [lines[0], json.dumps(json.loads(lines[1]) | {"accession": "x"}), *lines[2:]]):
        led.path.write_text("\n".join(bad) + "\n")
        with pytest.raises(LedgerError):
            led.verify()
        with pytest.raises(LedgerError):
            led.append("run")
