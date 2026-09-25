"""SEC XBRL numbers: first-reported values, split-safe comparatives, Q4 derivation, and the SEC tool never sees a
filing made on or after the release date."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_features import sec_tool
from build_xbrl_eps import facts_for, prior_of


def row(start, end, val, filed, accn, form="10-Q"):
    return {"start": start, "end": end, "val": val, "filed": filed, "accn": accn, "form": form}


def test_first_report_wins_and_the_filing_s_own_comparative_is_used_for_the_prior():
    rows = [row("2023-01-01", "2023-03-31", 2.00, "2023-05-01", "a1"),
            # a 2:1 split later: the 2024 10-Q restates the 2023 quarter as 1.00 and reports 1.20
            row("2024-01-01", "2024-03-31", 1.20, "2024-05-01", "a2"),
            row("2023-01-01", "2023-03-31", 1.00, "2024-05-01", "a2")]
    q, comps = facts_for(rows)
    assert q["2023-03-31"]["val"] == 2.00  # as first reported
    p = prior_of(q, "2024-03-31", comps, "a2")
    assert p["val"] == 1.00 and p["restated_in_filing"]
    assert prior_of(q, "2024-03-31")["val"] == 2.00  # without the filing's comparative: first report


def test_q4_is_the_annual_value_minus_three_quarters_known_at_the_10k():
    rows = [row("2024-01-01", "2024-03-31", 1.0, "2024-05-01", "q1"),
            row("2024-04-01", "2024-06-30", 1.0, "2024-08-01", "q2"),
            row("2024-07-01", "2024-09-30", 1.0, "2024-11-01", "q3"),
            row("2024-01-01", "2024-12-31", 4.5, "2025-02-20", "k", form="10-K")]
    q, _ = facts_for(rows)
    assert q["2024-12-31"]["val"] == pytest.approx(1.5)
    assert q["2024-12-31"]["derived"] and q["2024-12-31"]["filed"] == "2025-02-20"


def test_sec_tool_uses_only_filings_before_the_release():
    hist = pd.DataFrame([
        {"end": "2024-03-31", "filed": "2024-05-01", "eps": 1.0, "eps_prior": 0.9, "rev": 100.0, "rev_prior": 90.0},
        {"end": "2024-06-30", "filed": "2024-08-01", "eps": 1.1, "eps_prior": 1.0, "rev": 110.0, "rev_prior": 95.0},
        {"end": "2024-09-30", "filed": "2024-11-01", "eps": 1.2, "eps_prior": 1.0, "rev": 115.0, "rev_prior": 99.0},
        {"end": "2024-12-31", "filed": "2025-02-20", "eps": 1.2, "eps_prior": 1.1, "rev": 118.0, "rev_prior": 99.0},
        {"end": "2025-03-31", "filed": "2025-05-01", "eps": 1.3, "eps_prior": 1.0, "rev": 120.0, "rev_prior": 100.0},
    ])
    # a release on 2025-04-25 reports the quarter ended 2025-03-31; its 10-Q (filed 2025-05-01) is not visible yet
    got = sec_tool(hist, "2025-04-25T20:05:00+00:00")
    assert [h["end"] for h in got["history"]] == ["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31"]
    assert got["prior"]["end"] == "2024-03-31" and got["prior"]["eps"] == 1.0  # the released quarter a year earlier
    # a filing made on the release day itself is not visible either
    assert sec_tool(hist, "2025-05-01T12:00:00+00:00")["history"][-1]["end"] == "2024-12-31"
