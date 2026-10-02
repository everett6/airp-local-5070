"""Fact sheet v2 (PLAN_60_V2): the year-earlier figure is reconciled with the SEC-filed quarters."""
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from app.sandbox.reconcile import agrees, reconcile, same_figure

BACKEND = Path(__file__).resolve().parents[1]
YEAR_AGO = {"end": "2025-08-28", "eps": 2.83, "rev": 11315.0}
LAST = {"end": "2026-05-28", "eps": 24.67, "rev": 41456.0}


def test_the_previous_quarter_taken_for_the_year_earlier_one_is_replaced():
    """Micron, 30 Sep 2026: "54,229M vs 41,456M a year earlier (+30.8%)"; 41,456M was the quarter before."""
    pair, status, line = reconcile("revenue", {"q": 54229.0, "prior": 41456.0, "quote": "x"}, YEAR_AGO, LAST)
    assert (pair["prior"], pair["q"], pair["quote"], status) == (11315.0, 54229.0, "x", "previous_quarter")
    assert "41,456M" in line and "ended 2026-05-28" in line and "ended 2025-08-28" in line and "11,315M" in line
    pair, status, line = reconcile("eps", {"q": 32.87, "prior": 24.67}, YEAR_AGO, LAST)
    assert (pair["prior"], status) == (2.83, "previous_quarter") and "24.67" in line and "2.83" in line


def test_a_figure_that_agrees_or_cannot_be_checked_is_left_alone():
    for pair in ({"q": 54229.0, "prior": 11315.0}, {"q": 54229.0, "prior": 12300.0}):  # exact, and within 10%
        assert reconcile("revenue", pair, YEAR_AGO, LAST) == (pair, "agrees", None)
    assert reconcile("eps", {"q": 3.0, "prior": 2.85}, YEAR_AGO, LAST)[1] == "agrees"
    assert reconcile("eps", {"q": 0.05, "prior": 0.03}, {"end": "e", "eps": 0.04, "rev": None}, LAST)[1] == "agrees"
    for pair, ya in (({"q": 1.0}, YEAR_AGO), ({"q": 1.0, "prior": 2.0}, None), ({}, YEAR_AGO),
                     ({"q": 1.0, "prior": 2.0}, {"end": "e", "eps": 1.0, "rev": float("nan")})):
        assert reconcile("revenue", pair, ya, LAST) == (pair, "unchecked", None)


def test_any_other_difference_is_stated_not_replaced():
    """A bank's "total net revenue" against the SEC tag's revenue: another definition, not a mistake."""
    pair = {"q": 15000.0, "prior": 13000.0}
    out, status, line = reconcile("revenue", pair, YEAR_AGO, LAST)
    assert out == pair and status == "differs" and "13,000M" in line and "11,315M" in line
    assert "another definition" in line
    # close to the previous quarter but not it: still only stated
    assert reconcile("revenue", {"q": 1.0, "prior": 41000.0}, YEAR_AGO, LAST)[1] == "differs"
    assert reconcile("revenue", {"q": 1.0, "prior": 41456.0}, YEAR_AGO, None)[1] == "differs"  # no latest quarter


def test_tolerances():
    assert agrees("revenue", 109.9, 100.0) and not agrees("revenue", 110.2, 100.0) and not agrees("revenue", 1.0, 0.0)
    assert agrees("eps", 0.119, 0.10) and not agrees("eps", 0.13, 0.10) and agrees("eps", 5.4, 5.0)
    assert same_figure("revenue", 100.4, 100.0) and not same_figure("revenue", 100.6, 100.0)
    assert same_figure("eps", 1.004, 1.0) and not same_figure("eps", 1.01, 1.0)


def _build(tmp_path: Path, version: int) -> pd.DataFrame:
    """build_features.py on one made-up company whose reader took the previous quarter for the year-earlier one."""
    days = pd.bdate_range("2025-01-01", "2026-10-09")
    px = pd.DataFrame([{"Date": d.date().isoformat(), "Ticker": t, "Open": 100.0, "High": 100.0, "Low": 100.0,
                        "Close": 100.0, "Volume": 1} for d in days for t in ("ZZZ", "XLK", "SPY")])
    px.to_parquet(tmp_path / "px.parquet")
    pd.DataFrame([{"cik": 1, "ticker": "ZZZ", "sector": "Information Technology", "accession": "a1",
                   "accepted_utc": "2026-09-30T20:02:22", "filed": "2026-09-30"}]).to_csv(tmp_path / "ev.csv", index=False)
    (tmp_path / "ex.jsonl").write_text(json.dumps({
        "accession": "a1", "ticker": "ZZZ", "cik": 1, "period_end": "2026-08-27",
        "revenue": {"q": 54229.0, "prior": 41456.0}, "eps": {"q": 32.87}, "adj_eps": {"q": 33.42},
        "guidance": "raised", "tone": "positive", "highlights": []}) + "\n")
    xb = [{"cik": 1, "end": e, "fy": 2026, "fp": "Q", "form": "10-Q", "filed": f, "accn": "x", "eps": eps,
           "eps_prior": None, "eps_prior_filed": None, "prior_restated": False, "rev": rev, "rev_prior": None,
           "derived": False}
          for e, f, eps, rev in (("2025-08-28", "2025-10-03", 2.83, 11315.0), ("2025-11-27", "2025-12-18", 4.60, 13643.0),
                                 ("2026-02-26", "2026-03-19", 12.07, 23860.0), ("2026-05-28", "2026-06-25", 24.67, 41456.0))]
    pd.DataFrame(xb).to_csv(tmp_path / "xb.csv", index=False)
    name = f"pytest_reconcile_v{version}"
    out = BACKEND / "results" / "events" / f"features_{name}.csv"
    try:
        subprocess.run([sys.executable, "scripts/build_features.py", "--events", str(tmp_path / "ev.csv"), "--extract",
                        str(tmp_path / "ex.jsonl"), "--xbrl", str(tmp_path / "xb.csv"), "--prices",
                        str(tmp_path / "px.parquet"), "--name", name, *(["--sheet-version", "2"] if version == 2 else [])],
                       cwd=BACKEND, check=True, capture_output=True, text=True)
        return pd.read_csv(out)
    finally:
        out.unlink(missing_ok=True)


def test_the_builder_writes_the_corrected_sheet_only_in_version_2(tmp_path):
    v1, v2 = _build(tmp_path, 1), _build(tmp_path, 2)
    s1, s2 = v1["fact_sheet"].iloc[0], v2["fact_sheet"].iloc[0]
    assert "Revenue: 54,229M vs 41,456M a year earlier (+30.8%)" in s1 and "SEC reconciliation" not in s1
    assert "sheet_version" not in v1.columns  # version 1 is exactly what it was
    assert "Revenue: 54,229M vs 11,315M a year earlier (+379.3%)" in s2
    assert "SEC reconciliation: year-earlier revenue corrected: the release's comparison figure (41,456M)" in s2
    assert [x for x in s1.splitlines() if not x.startswith("Revenue:")] == [
        x for x in s2.splitlines() if not x.startswith(("Revenue:", "SEC reconciliation:"))]  # nothing else moves
    row = v2.iloc[0]
    assert (row["sheet_version"], row["rev_reconcile"], row["eps_reconcile"], row["rev_prior"]) == (
        2, "previous_quarter", "unchecked", 11315.0)
    f = json.loads(row["figures"])
    assert f["revenue"] == {"q": 54229.0, "period_end": "2026-08-27", "units": "USD millions",
                            "basis": "revenue as the release states it",
                            "source": "release (its quote was checked by code when it was read)",
                            "prior": 11315.0, "prior_period_end": "2025-08-28",
                            "prior_source": "SEC filing", "reconcile": "previous_quarter"}
    assert f["eps"]["prior_source"] == "SEC filing" and f["eps"]["basis"] == "GAAP, diluted"  # filled by the SEC tool
    assert f["adj_eps"]["prior"] is None and f["adj_eps"]["prior_source"] is None
