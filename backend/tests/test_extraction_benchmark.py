"""The extraction benchmark's scorer (scripts/extraction_benchmark.py) and the gold file's shape."""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import extraction_benchmark as B

CASE = {"ticker": "AAA", "accession": "a", "categories": ["loss"], "period_end": "2026-06-30",
        "revenue": {"q": 24560, "prior": 22749, "traps": {"six_months": 46777},
                    "other_basis": {"managed": {"q": 25000, "prior": 23000}}},
        "eps": {"q": -0.67, "prior": -0.92, "traps": {"prior_quarter": -0.31}},
        "adj_eps": {"q": None, "absent": True}, "guidance": "raised"}


def rec(**kw) -> dict:
    base = {"accession": "a", "model": "m", "period_end": "2026-06-30", "revenue": {"q": 24560, "prior": 22749},
            "eps": {"q": -0.67, "prior": -0.92}, "adj_eps": {}, "guidance": "raised"}
    return base | kw


def test_a_number_is_judged_by_what_kind_of_mistake_it_is() -> None:
    assert set(B.score_case(CASE, rec()).values()) == {"correct", "correct_absent"}
    got = B.score_case(CASE, rec(revenue={"q": 24.56, "prior": 46777}, eps={"q": 0.67, "prior": -0.31},
                                 adj_eps={"q": 1.2}, guidance="lowered", period_end="2026-03-31"))
    assert got == {"period_end": "wrong_period", "revenue.q": "wrong_units", "revenue.prior": "wrong_period",
                   "eps.q": "wrong_sign", "eps.prior": "wrong_period", "adj_eps.q": "invented", "guidance": "wrong"}
    got = B.score_case(CASE, rec(revenue={"q": 25000, "prior": 24560}, eps={"q": None, "prior": 5.0}))
    assert got["revenue.q"] == "wrong_basis" and got["revenue.prior"] == "wrong_period"  # this year's as last year's
    assert got["eps.q"] == "missed" and got["eps.prior"] == "wrong"
    assert B.score_case(CASE, None)["revenue.q"] == "missed" and B.score_case(CASE, None)["adj_eps.q"] == "correct_absent"
    # GAAP returned where the adjusted figure was asked for is a basis mistake
    two = {"ticker": "B", "accession": "b", "categories": [], "eps": {"q": 4.62}, "adj_eps": {"q": 6.13}}
    assert B.score_case(two, {"eps": {"q": 6.13}, "adj_eps": {"q": 4.62}}) == {"eps.q": "wrong_basis",
                                                                              "adj_eps.q": "wrong_basis"}
    assert B.close(112.0e3, 111.6e3, False) and not B.close(112.0e3, 110.0e3, False) and not B.close(2.00, 2.01, True)


def test_the_summary_counts_by_dimension() -> None:
    gold = {"version": 1, "cases": [CASE, {**CASE, "ticker": "BBB", "accession": "b"},
                                    {**CASE, "ticker": "CCC", "accession": "c"}]}
    records = {"a": rec(), "b": rec(accession="b", revenue={"q": 46777, "prior": None}, adj_eps={"q": 0.5}),
               "c": {"accession": "c", "model": "none"}}
    r = B.score(gold, records)
    assert (r["scored_cases"], r["not_read"], r["fields"], r["right"]) == (2, ["CCC"], 14, 11)
    d = r["dimensions"]
    assert d["period"]["figures_from_another_period"] == 1 and d["period"]["period_end"] == {"scored": 2, "right": 2}
    assert d["missing_information"] == {"stated_and_found": 10, "stated": 12, "stated_and_missed": 1,
                                        "absent_and_said_so": 1, "absent": 2, "invented": 1}
    assert r["by_category"] == {"loss": {"right": 11, "scored": 14}} and d["guidance"] == {"scored": 2, "right": 2}


def test_the_gold_file_is_well_formed_and_its_releases_are_on_disk() -> None:
    gold = json.loads(B.GOLD.read_text())
    cats = {c for case in gold["cases"] for c in case["categories"]}
    assert {"bank", "odd_fiscal_year", "loss", "changed_guidance", "heavy_tables", "missing_information",
            "accounting_basis"} <= cats and len(gold["cases"]) >= 12
    assert len({c["accession"] for c in gold["cases"]}) == len(gold["cases"])
    events = (B.GOLD.parent / "events.csv").read_text()
    for c in gold["cases"]:
        assert c["accession"] in events and c["notes"]
        assert any(f in c for f in B.FIELDS)
        for f in B.FIELDS:
            g = c.get(f, {})
            assert set(g) <= {"q", "prior", "absent", "traps", "basis", "other_basis"}
            assert ("q" in g and g["q"] is None) == bool(g.get("absent"))
        assert c.get("guidance") in (None, "raised", "lowered", "maintained", "initiated", "withdrawn", "none")
    text = Path(B.BACKEND / "data" / "events" / "text")
    if text.exists():  # the releases themselves are local data; where they are, a labelled figure must be in them
        mu = next(c for c in gold["cases"] if c["ticker"] == "MU")
        body = gzip.decompress((text / f"{mu['accession']}.txt.gz").read_bytes()).decode()
        assert "54,229" in body and "11,315" in body and "32.87" in body
