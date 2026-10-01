"""Treasury auction dates for A1 (docs/PLAN_60_V2.md "A1, Treasury auction cycle"): nominal 10-year note and
30-year bond auctions, first issues and reopenings, from TreasuryDirect's public auction list (free, no key).

    python scripts/auction_calendar.py        # writes data/macro/treasury_auctions.csv

Dates only: no prices are read here.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import pandas as pd

URL = "https://www.treasurydirect.gov/TA_WS/securities/search?format=json&type={kind}"
OUT = BACKEND / "data" / "macro" / "treasury_auctions.csv"
KEEP = {"Note": "10-Year", "Bond": "30-Year"}


def rows(items: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    """The auctions of one list that belong to the calendar: nominal, the kept original term, already held."""
    out = []
    for x in items:
        orig = x.get("originalSecurityTerm") or x.get("securityTerm") or ""
        if (x.get("securityType") != kind or orig != KEEP[kind] or x.get("tips") == "Yes"
                or x.get("floatingRate") == "Yes" or not x.get("auctionDate")):
            continue
        out.append({"date": x["auctionDate"][:10], "original_term": orig, "term": x.get("securityTerm", ""),
                    "reopening": x.get("reopening", ""), "cusip": x.get("cusip", "")})
    return out


def fetch(kind: str) -> list[dict[str, Any]]:
    req = urllib.request.Request(URL.format(kind=kind), headers={"User-Agent": "airp-research (paper trading research)"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data: list[dict[str, Any]] = json.loads(r.read())
    return data


def main() -> None:
    got = [r for kind in KEEP for r in rows(fetch(kind), kind)]
    df = pd.DataFrame(got).drop_duplicates(["date", "cusip"]).sort_values(["date", "original_term"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)
    by = df.assign(year=df["date"].str[:4]).groupby(["year", "original_term"]).size().unstack(fill_value=0)
    print(f"{len(df)} auctions, {df['date'].min()} .. {df['date'].max()} -> {OUT.relative_to(BACKEND)}")
    print(by.tail(14).to_string())


if __name__ == "__main__":
    main()
