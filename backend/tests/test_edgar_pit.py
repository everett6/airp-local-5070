"""EDGAR fundamentals: nothing filed on or after a cutoff is visible at that cutoff (offline, synthetic facts)."""
from datetime import UTC, date, datetime

import pytest

from app.data_ingestion import edgar as E
from app.sandbox.clock import LookaheadViolation, sandbox_scope


def _q(y, q, val, filed):
    ends = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}
    starts = {1: "01-01", 2: "04-01", 3: "07-01", 4: "10-01"}
    return (f"{y}-{starts[q]}", f"{y}-{ends[q]}", val, filed)


def _history():
    """Quarterly EPS 2021..2025 growing 0.10/yr with a seasonal bump; filed ~35 days after quarter end."""
    facts = []
    fq = {1: "05-05", 2: "08-05", 3: "11-05", 4: None}
    for y in range(2021, 2026):
        for q in (1, 2, 3):
            facts.append(_q(y, q, 1.0 + 0.1 * (y - 2021) + 0.05 * q, f"{y}-{fq[q]}"))
        # Q4 only via the annual 10-K (FY minus the three quarters), filed early next year
        fy = sum(1.0 + 0.1 * (y - 2021) + 0.05 * q for q in (1, 2, 3)) + (1.0 + 0.1 * (y - 2021) + 0.2)
        facts.append((f"{y}-01-01", f"{y}-12-31", fy, f"{y + 1}-02-20"))
    return facts


def test_value_filed_on_the_cutoff_is_invisible_the_day_before_it_is_visible():
    facts = _history()
    assert E.quarters_as_of(facts, date(2025, 8, 5))[-1].end == date(2025, 3, 31)  # Q2 filed that day: not yet
    assert E.quarters_as_of(facts, date(2025, 8, 6))[-1].end == date(2025, 6, 30)


def test_restatement_counts_only_from_its_own_filing_date():
    facts = [*_history(), _q(2025, 1, 9.99, "2025-09-01")]  # Q1 2025 restated in a later filing
    before = E.quarters_as_of(facts, date(2025, 8, 20))
    after = E.quarters_as_of(facts, date(2025, 9, 2))
    q1 = date(2025, 3, 31)
    assert next(q for q in before if q.end == q1).value == pytest.approx(1.45)
    assert next(q for q in after if q.end == q1).value == 9.99
    assert next(q for q in after if q.end == q1).first_filed == date(2025, 5, 5)  # "first public" date unchanged


def test_q4_is_derived_from_the_annual_report_only_once_it_is_filed():
    facts = _history()
    qs = E.quarters_as_of(facts, date(2025, 2, 21))
    q4 = qs[-1]
    assert q4.end == date(2024, 12, 31) and q4.value == pytest.approx(1.0 + 0.3 + 0.2)
    assert q4.first_filed == date(2025, 2, 20)
    assert E.quarters_as_of(facts, date(2025, 2, 20))[-1].end == date(2024, 9, 30)


def test_sue_is_standardized_and_needs_history():
    facts = _history()
    qs = E.quarters_as_of(facts, date(2026, 1, 1))
    sues = E.sue_series(qs)
    assert all(s is None for s in sues[:8])  # first year has no year-ago value; then too few changes
    # every year-over-year change is exactly +0.10, so the spread is 0 and SUE is undefined, not infinite
    assert all(s is None for s in sues)
    noisy = [(s, e, v + (0.03 if i % 3 == 0 else -0.02), f) for i, (s, e, v, f) in enumerate(facts)]
    later = [x for x in E.sue_series(E.quarters_as_of(noisy, date(2026, 1, 1))) if x is not None]
    assert later and all(-5 <= x <= 5 for x in later)


def _doc(facts, filings):
    return {"companies": {"ACME": {"cik": 1, "filings": filings, "eps": {"tag": "EarningsPerShareDiluted",
                                                                          "facts": facts},
                                   "revenue": {"tag": "Revenues", "facts": facts}}}}


def test_features_ignore_filings_on_or_after_the_cutoff_and_flag_expected_earnings():
    filings = [["8-K", "2024-08-01", "2.02,9.01"], ["10-Q", "2024-08-05", ""], ["8-K", "2025-07-31", "2.02"],
               ["10-Q", "2025-08-05", ""], ["8-K", "2025-08-20", "5.02"]]
    pit = E.PITFundamentals(_doc(_history(), filings))
    c = date(2025, 7, 28)
    with sandbox_scope(as_of=datetime(2025, 7, 28, tzinfo=UTC), run_id="t"):
        f = pit.features("ACME", c, horizon_days=5)
    assert f["days_since_report"] == (c - date(2025, 5, 5)).days  # Q1 report; Q2 not yet filed
    assert f["days_since_earnings_8k"] == 180  # no earnings 8-K before the cutoff inside the history
    assert f["earnings_expected"] == 1.0  # last year's release on 2024-08-01 falls inside the next ~10 days
    assert f["n_8k_30d"] == 0
    c2 = date(2025, 8, 21)
    with sandbox_scope(as_of=datetime(2025, 8, 21, tzinfo=UTC), run_id="t"):
        f2 = pit.features("ACME", c2, horizon_days=5)
    assert f2["days_since_earnings_8k"] == 21 and f2["n_8k_30d"] == 2 and f2["earnings_expected"] == 0.0


def test_a_leaky_filter_would_abort_not_leak(monkeypatch):
    pit = E.PITFundamentals(_doc(_history(), [["10-Q", "2025-08-05", ""]]))
    # simulate a bug: the quarter builder ignores the cutoff
    monkeypatch.setattr(E, "quarters_as_of", lambda facts, cutoff: E.Quarter(date(2025, 6, 30), 1.0,
                                                                              date(2025, 8, 5)) and
                        [E.Quarter(date(2025, 6, 30), 1.0, date(2025, 8, 5))])
    with sandbox_scope(as_of=datetime(2025, 8, 1, tzinfo=UTC), run_id="t"), pytest.raises(LookaheadViolation):
        pit.features("ACME", date(2025, 8, 1))


def test_unknown_company_and_digest_have_no_identity():
    pit = E.PITFundamentals({"companies": {}})
    with sandbox_scope(as_of=datetime(2025, 8, 1, tzinfo=UTC), run_id="t"):
        f = pit.features("NOPE", date(2025, 8, 1))
    assert f["fund_ok"] == 0.0 and "not enough" in E.digest_text(f)
    text = E.digest_text({"fund_ok": 1, "eps_yoy": 0.1, "rev_yoy": 0.2, "sue_1": 1, "sue_2": 0, "sue_3": 0,
                          "sue_4": 0, "days_since_report": 10, "days_since_earnings_8k": 12, "n_8k_30d": 1,
                          "earnings_expected": 1})
    assert "ACME" not in text and "20" not in text.split("revenue")[0][-10:]  # numbers only, no names/dates
