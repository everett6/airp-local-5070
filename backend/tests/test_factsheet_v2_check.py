import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import factsheet_v2_check as F

XB = pd.DataFrame([  # one company: quarters filed before the release, and its own quarter filed after
    {"cik": 1, "end": "2026-02-26", "filed": "2026-03-19", "rev_prior": 8053.0, "eps_prior": 1.41},
    {"cik": 1, "end": "2026-05-28", "filed": "2026-06-25", "rev_prior": 9301.0, "eps_prior": 1.68},
    {"cik": 1, "end": "2026-08-27", "filed": "2026-10-05", "rev_prior": 11315.0, "eps_prior": 2.83},
    {"cik": 1, "end": "2026-11-26", "filed": "2026-12-20", "rev_prior": 13643.0, "eps_prior": 4.60}])


def test_the_later_filed_comparative_is_the_releases_own_quarter():
    assert F.later_comparative(XB, "2026-09-30T20:02:22") == {"rev": 11315.0, "eps": 2.83}
    assert F.later_comparative(XB, "2026-12-21T12:00:00") == {"rev": None, "eps": None}  # its quarter is not filed yet
    assert F.later_comparative(XB, "2026-01-05T12:00:00") == {"rev": None, "eps": None}  # nothing filed before it
    assert F.matches("rev", 11400.0, 11315.0) and not F.matches("rev", 41456.0, 11315.0)
    assert F.matches("eps", 2.835, 2.83) and not F.matches("eps", 2.85, 2.83)


def test_truer_figures_counts_each_version_against_the_later_filing():
    base = {"cik": 1, "accepted_utc": "2026-09-30T20:02:22"}
    v1 = pd.DataFrame([{**base, "accession": "a", "rev_prior": 41456.0, "eps_prior": 2.83},
                       {**base, "accession": "b", "rev_prior": 11315.0, "eps_prior": np.nan}])
    v2 = pd.DataFrame([{**base, "accession": "a", "rev_prior": 11315.0, "eps_prior": 2.83,
                        "rev_reconcile": "previous_quarter", "eps_reconcile": "agrees"},
                       {**base, "accession": "b", "rev_prior": 11315.0, "eps_prior": np.nan,
                        "rev_reconcile": "agrees", "eps_reconcile": "unchecked"}])
    t = F.truth(v1, v2, XB)
    assert t["rev"] == {"checked": 2, "right_v1": 0.5, "right_v2": 1.0}
    assert t["eps"] == {"checked": 1, "right_v1": 1.0, "right_v2": 1.0}
    assert t["replaced"] == {"n": 1, "right": 1, "share_right": 1.0}


def _table(shift: float) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    rows = []
    for m in range(6):
        for i in range(30):
            s = rng.normal()
            rows.append({"accession": f"{m}-{i}", "sample": "2024" if m < 3 else "2025-26", "month": f"2025-{m + 1:02d}",
                         "v1": s, "v2": s + shift * rng.normal(), "fwd5": 0.02 * s + 0.05 * rng.normal(),
                         "sheet_v1": "x", "sheet_v2": "x" if i % 5 else "y"})
    return pd.DataFrame(rows)


def test_the_judge_side_and_the_verdict():
    same = F.compare(_table(0.0))
    assert same["d_mean"] == 0 and same["d_ci95"] == [0.0, 0.0] and same["no_worse"] and same["rank_corr_v1_v2"] == 1
    assert same["changed_sheets"]["n"] == 36 and same["months"] == 6 and same["call_flips_pct"] == 0
    noisy = F.compare(_table(5.0))  # v2 scores mostly noise: the judge is worse
    assert noisy["ic_v2"] < noisy["ic_v1"] and not noisy["no_worse"]
    good = {"rev": {"checked": 9, "right_v1": 0.8, "right_v2": 0.9}, "eps": {"checked": 9, "right_v1": 0.9, "right_v2": 0.9},
            "replaced": {"n": 5, "right": 4, "share_right": 0.8}}
    assert F.verdict(same, good) == {"truer_figures": True, "no_worse_for_the_judge": True, "pass": True}
    assert not F.verdict(noisy, good)["pass"]
    worse = {**good, "eps": {"checked": 9, "right_v1": 0.9, "right_v2": 0.89}}
    assert not F.verdict(same, worse)["pass"]
    assert not F.verdict(same, {**good, "replaced": {"n": 5, "right": 3, "share_right": 0.6}})["pass"]
    assert F.verdict(same, {**good, "replaced": {"n": 0, "right": 0, "share_right": None}})["pass"]
