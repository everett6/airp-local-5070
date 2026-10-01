"""Arm B4's sample (docs/PLAN_60_V2.md "Arm B4"): the S&P 400/600 releases of the breadth test accepted
2026-01-01 .. 2026-08-24. Writes data/events/events_b4_2026.csv and results/events/features_b4_2026.csv.

    python scripts/b4_prep.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
FROM, TO = "2026-01-01", "2026-08-25"  # accepted_utc in [FROM, TO)


def main() -> None:
    f = pd.read_csv(BACKEND / "results" / "events" / "features_breadth_2025.csv")
    f = f[(f["accepted_utc"] >= FROM) & (f["accepted_utc"] < TO)]
    e = pd.read_csv(BACKEND / "data" / "events" / "events_breadth_2025.csv")
    e = e[e["accession"].isin(set(f["accession"]))]
    f.to_csv(BACKEND / "results" / "events" / "features_b4_2026.csv", index=False)
    e.to_csv(BACKEND / "data" / "events" / "events_b4_2026.csv", index=False)
    print(f"B4 sample: {len(f)} releases with features, {len(e)} events")


if __name__ == "__main__":
    main()
