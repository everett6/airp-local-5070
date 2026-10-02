import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import m1_shadow

from app.sandbox.flows import month_end_records, month_end_verdict

DAYS = pd.bdate_range("2026-09-01", "2026-12-04")


def closes(until: str = "2026-12-04") -> pd.Series:
    """Flat at 100, except +1% on each of October's last three days and -1% on November's last day."""
    r = pd.Series(0.0, index=DAYS)
    r[["2026-10-28", "2026-10-29", "2026-10-30"]] = 0.01
    r["2026-11-30"] = -0.01
    return (100 * (1 + r).cumprod()).loc[:until]


RF = pd.Series(0.0252, index=pd.to_datetime(["2026-08-03"]))  # 1 bp a day


def test_one_record_per_finished_month_from_october():
    recs = month_end_records(closes(), RF, "2026-10")
    assert [r["month"] for r in recs] == ["2026-10", "2026-11"]  # September is before the start, December unfinished
    o = recs[0]
    assert o["days"] == ["2026-10-28", "2026-10-29", "2026-10-30"]
    assert o["excess"] == pytest.approx([0.0099] * 3) and o["net"] == pytest.approx(0.0297 - 0.0002)
    assert o["other_n"] == 19 and o["other_sum"] == pytest.approx(-19e-4)
    assert recs[1]["net"] == pytest.approx(-0.01 - 3e-4 - 2e-4)
    assert month_end_records(closes("2026-10-30"), RF, "2026-10") == []  # no later month in the data yet


def test_collect_appends_once_and_never_rewrites(tmp_path):
    early = datetime(2026, 10, 20, 20, 0, tzinfo=UTC)
    assert m1_shadow.due([], early) == [] and m1_shadow.collect(tmp_path, early) == []  # nothing due: no download
    nov = datetime(2026, 11, 3, 13, 0, tzinfo=UTC)
    assert m1_shadow.collect(tmp_path, nov, closes("2026-11-02"), RF) == ["2026-10"]
    first = (tmp_path / "ledger.jsonl").read_text()
    assert m1_shadow.collect(tmp_path, nov, closes("2026-11-02"), RF) == []
    dec = datetime(2026, 12, 2, 13, 0, tzinfo=UTC)
    assert m1_shadow.collect(tmp_path, dec, closes("2026-11-30"), RF) == []  # November not finished in the data
    assert m1_shadow.collect(tmp_path, dec, closes(), RF) == ["2026-11"]
    text = (tmp_path / "ledger.jsonl").read_text()
    assert text.startswith(first) and len(text.splitlines()) == 2
    st = m1_shadow.status(tmp_path)
    assert st["months"] == 2 and st["hit_rate"] == 0.5 and not st["ready_to_judge"] and st["last"] == "2026-11"
    with pytest.raises(ValueError, match="data check"):
        m1_shadow.collect(tmp_path, datetime(2027, 1, 5, 13, 0, tzinfo=UTC), closes() * 0, RF)


def test_verdict_numbers_and_the_24_month_rule(tmp_path, monkeypatch):
    rng = np.random.default_rng(1)
    recs = [{"type": "month", "month": str(pd.Period("2026-10", "M") + k), "excess": list(rng.normal(0.002, 0.005, 3)),
             "other_sum": float(rng.normal(0, 0.02)), "other_n": 18} for k in range(24)]
    for r in recs:
        r["net"] = sum(r["excess"]) - 2e-4
    v = month_end_verdict(recs)
    daily = np.concatenate([np.r_[r["excess"][0] - 1e-4, r["excess"][1], r["excess"][2] - 1e-4, np.zeros(18)]
                            for r in recs])
    assert v["sharpe"] == pytest.approx(daily.mean() / daily.std() * np.sqrt(252), abs=1e-3)
    me, other = sum(sum(r["excess"]) for r in recs) / 72, sum(r["other_sum"] for r in recs) / (18 * 24)
    assert v["diff_bp"] == pytest.approx((me - other) * 1e4, abs=0.01) and v["diff_lo80_bp"] < v["diff_bp"]
    seen: list[dict] = []
    monkeypatch.setattr("app.sandbox.dsr.register", seen.append)
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("".join(json.dumps(r) + "\n" for r in recs[:23]))
    with pytest.raises(SystemExit, match="23 of 24"):
        m1_shadow.verdict(tmp_path, datetime(2028, 9, 2, tzinfo=UTC))
    assert seen == []
    ledger.write_text("".join(json.dumps(r) + "\n" for r in recs))
    res = m1_shadow.verdict(tmp_path, datetime(2028, 10, 3, tzinfo=UTC))
    assert res["pass"] == (v["sharpe"] >= 0.5 and v["diff_lo80_bp"] > 0) and seen[0]["trial"] == m1_shadow.TRIAL
    with pytest.raises(SystemExit, match="already run"):
        m1_shadow.verdict(tmp_path, datetime(2028, 10, 4, tzinfo=UTC))
    assert len(seen) == 1
