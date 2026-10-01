"""Forward check of the two core leads (docs/PLAN_60_V2.md "Core leads, forward check", fixed 2026-09-29).

    python scripts/core_leads.py          # print the forward comparison so far (JSON); changes nothing

B0, B0 + the 20% vol target (vol_target_b0.managed) and CVaR risk parity (skfolio_test.arm_s) are recomputed with
their trials' frozen code on data through yesterday; only days from FORWARD_FROM count. Both rules use trailing data
only, so recomputing never looks ahead. Judged at the first Saturday review on or after VERDICT_ON.
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import pandas as pd

FORWARD_FROM, VERDICT_ON = "2026-09-30", date(2027, 9, 30)


def forward(today: date | None = None) -> dict[str, Any]:
    import skfolio_test
    from trend_sleeve import master_b0
    from vol_target_b0 import compare, managed, tbill
    today = today or datetime.now(UTC).date()
    end = (today - timedelta(days=1)).isoformat()
    b0 = master_b0("2018-01-02", end)
    vt, _ = managed(b0, tbill())
    skfolio_test.END = end
    rp, _ = skfolio_test.arm_s()
    both = pd.concat([b0.rename("B0"), vt.rename("VT"), rp.rename("RP")], axis=1, join="inner").loc[FORWARD_FROM:]
    out: dict[str, Any] = {"forward_from": FORWARD_FROM, "days": len(both), "verdict_due": today >= VERDICT_ON}
    if len(both) < 30:
        out["note"] = "fewer than 30 forward days: nothing to compare yet"
        return out
    for key, col in (("vol_target", "VT"), ("risk_parity", "RP")):
        c = compare(both["B0"], both[col])
        out[key] = {"sharpe_diff": c["sharpe_diff"], "ci90": c["sharpe_diff_ci90"],
                    "cagr_vol_matched_pct": c["managed_vol_matched"]["cagr_pct"], "b0_cagr_pct": c["B0"]["cagr_pct"],
                    "would_pass": c["pass"]}
    return out


if __name__ == "__main__":
    print(json.dumps(forward(), indent=1))
