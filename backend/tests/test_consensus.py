"""The consensus shadow (PLAN_60_V2 "Consensus shadow"): votes, record-based weights, no look-ahead, written once."""
from __future__ import annotations

import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import consensus_shadow as S

from app.sandbox import consensus as C


def test_votes_and_cold_start_is_the_plain_mean() -> None:
    v = C.votes(4.1, "raised", {"net_read": "bullish", "ai_read": "neutral", "bb_read": "bearish"})
    assert v == {"judge": 1, "net_read": 1, "ai_read": 0, "bb_read": -1, "guidance": 1}
    assert C.votes(None, "unverified", {}) == dict.fromkeys(C.AGENTS, 0)
    assert C.votes(-0.2, "withdrawn", {"net_read": "garbage"})["judge"] == -1
    got = C.combine(v, C.records([]))
    assert got["eq"] == got["rw"] == 0.4 and set(got["weights"].values()) == {0.1}


def test_a_proven_agent_outweighs_the_rest_and_a_bad_one_never_goes_negative() -> None:
    past = [({"judge": 1, "net_read": -1, "ai_read": 0, "bb_read": 0, "guidance": 0}, 0.02)] * 30
    rec = C.records(past)
    assert rec["judge"] == (30, 30) and rec["net_read"] == (0, 30) and rec["ai_read"] == (0, 0)
    assert math.isclose(C.weight(30, 30), 0.1 + math.log(4)) and C.weight(0, 30) == 0.1 == C.weight(0, 0)
    v = {"judge": 1, "net_read": -1, "ai_read": -1, "bb_read": -1, "guidance": -1}
    got = C.combine(v, rec)
    assert got["eq"] == -0.6 and got["rw"] > 0.5  # the judge alone, against four unproven agents
    assert C.records([(v, 0.0)]) == dict.fromkeys(C.AGENTS, (0, 0))  # a flat result is nobody's hit or miss


def _write(p: Path, rows: list[dict[str, Any]]) -> None:
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _dec(seq: int, acc: str, deadline: str, lo: float, guidance: str = "none") -> dict[str, Any]:
    return {"seq": seq, "type": "decision", "accession": acc, "ticker": "T" + acc, "entry_deadline": deadline,
            "logodds": lo, "guidance": guidance, "on_time": True, "written_at": "2026-10-01T12:00:00+00:00"}


def test_collect_is_on_time_only_once_only_and_never_looks_ahead(tmp_path: Path) -> None:
    d, out = tmp_path / "events", tmp_path / "consensus"
    d.mkdir()
    _write(d / "ledger.jsonl", [_dec(0, "a", "2026-10-02T09:30:00-04:00", 3.0, "raised"),
                               {"seq": 1, "type": "missed", "accession": "m"}])
    _write(d / "net_read.jsonl", [
        {"accession": "a", "entry_deadline": "2026-10-02T09:30:00-04:00", "net_read": "bearish",
         "written_at": "2026-10-02T14:00:00+00:00"},  # after the open: does not vote
        {"accession": "a", "entry_deadline": "2026-10-02T09:30:00-04:00", "net_read": "bullish",
         "written_at": "2026-10-01T12:05:00+00:00"}])
    _write(d / "bull_bear.jsonl", [{"accession": "a", "entry_deadline": "2026-10-02T09:30:00-04:00",
                                    "bb_read": "bearish", "written_at": "2026-10-01T12:05:00+00:00"}])
    t1 = datetime(2026, 10, 1, 12, 10, tzinfo=UTC)
    assert S.collect(d, out, t1) == 1 and S.collect(d, out, t1) == 0  # the second run adds nothing
    a = json.loads((out / "ledger.jsonl").read_text().splitlines()[0])
    assert a["votes"] == {"judge": 1, "net_read": 1, "ai_read": 0, "bb_read": -1, "guidance": 1}
    assert a["eq"] == a["rw"] == 0.4 and a["matured"] == 0

    # a's outcome is written on 9 Oct. b's entry was on 8 Oct: it must not see it. c's is on 12 Oct: it does.
    led = [json.loads(x) for x in (d / "ledger.jsonl").read_text().splitlines()]
    led += [_dec(2, "b", "2026-10-08T09:30:00-04:00", -1.0), _dec(3, "c", "2026-10-12T09:30:00-04:00", 2.0),
            {"seq": 4, "type": "outcome", "accession": "a", "entry": "2026-10-02", "fwd5": 0.03,
             "written_at": "2026-10-09T12:46:00+00:00"}]
    _write(d / "ledger.jsonl", led)
    before = (out / "ledger.jsonl").read_text()
    assert S.collect(d, out, datetime(2026, 10, 9, 12, 50, tzinfo=UTC)) == 2
    text = (out / "ledger.jsonl").read_text()
    assert text.startswith(before)  # earlier lines are never rewritten
    b, c = (json.loads(x) for x in text.splitlines()[1:])
    assert b["matured"] == 0 and b["records"]["judge"] == [0, 0]
    assert c["matured"] == 1 and c["records"] == {"judge": [1, 1], "net_read": [1, 1], "ai_read": [0, 0],
                                                  "bb_read": [0, 1], "guidance": [1, 1]}
    assert c["weights"]["judge"] > c["weights"]["bb_read"] == 0.1

    # b was written after its own entry deadline (9 Oct > 8 Oct): kept, never scored. a is scored.
    df = S.scored(d, out)
    assert list(df["accession"]) == ["a"] and df["fwd5"].iloc[0] == 0.03 and df["month"].iloc[0] == "2026-10"
    st = S.status(d, out)
    assert st["recorded"] == 3 and st["scored"] == 1 and st["consensus_rw"]["ready_to_judge"] is False
    assert st["agents"]["judge"] == {"hits": 1, "calls": 1, "weight": c["weights"]["judge"]}


def test_status_on_nothing(tmp_path: Path) -> None:
    st = S.status(tmp_path, tmp_path / "none")
    assert st == {"recorded": 0, "scored": 0,
                  "consensus_eq": {"months": 0, "mean_ic": None, "ic_lo80": None, "ready_to_judge": False},
                  "consensus_rw": {"months": 0, "mean_ic": None, "ic_lo80": None, "ready_to_judge": False}}
