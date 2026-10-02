"""The opportunity funnel (scripts/funnel.py): every release ends in exactly one place, with its reason."""
from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import funnel as F

NOW = datetime(2026, 10, 20, tzinfo=UTC)
ACC = "2026-10-01T20:10:00"  # after the close


def ev(*accs: str, url: dict[str, str | None] | None = None) -> pd.DataFrame:
    return pd.DataFrame([{"accession": a, "ticker": a.upper(), "sector": "Energy", "accepted_utc": ACC,
                          "filed": "2026-10-01", "ex99_url": (url or {}).get(a, "https://x")} for a in accs])


def dec(a: str, lo: float = 3.5, src: str = "bonsai") -> dict:
    return {"type": "decision", "accession": a, "on_time": True, "logodds": lo, "source": src}


def pair(a: str, status: str, legs: list[tuple[str, str, str, float | None]], **kw) -> dict:
    return {"accession": a, "ticker": a.upper(), "etf": "XLE", "qty": 10, "etf_qty": 20, "status": status,
            "legs": [{"client_order_id": f"airp-pk-{a}-{ph}-x", "asset": asset, "status": st,
                      "qty": 10 if asset != "XLE" else 20, "filled_qty": got} for ph, asset, st, got in legs], **kw}


def test_every_release_stops_in_one_place_with_its_reason() -> None:
    accs = ["nopr", "nodl", "late", "wait", "lite", "low", "skip", "unsent", "part", "open", "done", "noprice"]
    ledger = [
        {"type": "missed", "accession": "nopr", "reason": "no decision (no press release, fact sheet or model output)"},
        {"type": "missed", "accession": "late", "reason": "decided after the entry open: never backfilled"},
        dec("lite", src="lite"), dec("low", lo=1.0), dec("skip"), dec("unsent"), dec("part"), dec("open"),
        dec("done"), dec("noprice"),
        {"type": "outcome", "accession": "done", "fwd5": 0.02}, {"type": "outcome", "accession": "low", "fwd5": -0.01},
        {"type": "outcome", "accession": "noprice", "fwd5": None},
    ]
    book = {"threshold": 2.873, "pairs": [
        {"accession": "skip", "ticker": "SKIP", "etf": "XLE", "qty": 0, "etf_qty": 0, "status": "skipped",
         "note": "all 5 slots in use"},
        pair("unsent", "open", []),
        pair("part", "open", [("in", "PART", "canceled", 4.0), ("in", "XLE", "filled", 20.0)]),
        pair("open", "open", [("in", "OPEN", "filled", 10.0), ("in", "XLE", "filled", 20.0)]),
        pair("done", "closed", [("in", "DONE", "filled", 10.0), ("in", "XLE", "filled", 20.0)],
             broker_audit={"held": {}}, ret=0.03, late_exit="2026-10-09T12:45:00+00:00"),
    ]}
    has_text = set(accs) - {"nopr", "nodl"}
    extract = {a: {"accession": a, "model": "qwen3:8b"} for a in has_text - {"lite"}} | {"lite": {"model": "none"}}
    unseen = ev("ghost")
    rows = {r["accession"]: r for r in F.classify(ev(*accs, url={"nopr": None}), ledger, extract, has_text, book,
                                                  NOW, unseen)}
    assert rows["nopr"]["reached"][-1] == "discovered" and "no press release" in rows["nopr"]["stopped"]
    assert rows["nodl"]["stopped"] is None and "retried until its open" in rows["nodl"]["waiting"]
    assert rows["late"]["reached"][-1] == "extracted" and "after the entry open" in rows["late"]["stopped"]
    assert rows["wait"]["waiting"] == "its decision (the open is still ahead)"
    assert rows["lite"]["trade_stopped"] == "scored by Bonsai-lite (never traded)" and "note" in rows["lite"]
    assert rows["low"]["trade_stopped"] == "score below the threshold (2.873)" and rows["low"]["reached"][-1] == "evaluated"
    assert rows["skip"]["trade_stopped"] == "sleeve skipped it: all 5 slots in use"
    assert rows["unsent"]["trade"] == ["selected"] and rows["unsent"]["trade_stopped"] == "orders were never sent"
    assert rows["part"]["trade"] == ["selected", "submitted"] and "PART canceled (4 of 10)" in rows["part"]["trade_stopped"]
    assert rows["open"]["trade"][-1] == "filled" and rows["open"]["trade_waiting"] == "its exit"
    assert rows["done"]["trade"][-1] == "closed" and rows["done"]["note"] == "late exit at the broker"
    assert rows["noprice"]["stopped"].startswith("no outcome")
    assert rows["ghost"]["reached"] == ["eligible"] and "no run ever saw it" in rows["ghost"]["stopped"]
    for r in rows.values():  # one fate each on the decision track: stopped, waiting, or evaluated
        assert sum([bool(r["stopped"]), bool(r["waiting"]), r["reached"][-1] == "evaluated"]) == 1, r

    rep = F.summarise(list(rows.values()), swept=True)
    assert rep["stages"] == {"eligible": 13, "discovered": 12, "downloaded": 10, "extracted": 9,
                             "scored_on_time": 8, "evaluated": 2}
    assert rep["trade_stages"] == {"scored_on_time": 8, "selected": 4, "submitted": 3, "filled": 2, "closed": 1}
    assert rep["who_gets_scored"]["by_session"] == {"after the open": {"releases": 13, "scored_on_time": 8}}
    assert {"after": "eligible", "why": rows["ghost"]["stopped"], "releases": 1} in rep["stopped"]
