"""Breaking-news dataset for the 8-K watcher: every non-earnings 8-K with a news item from S&P 500 members, free SEC
data only. Looks at no prices or returns.

    python scripts/build_8k_events.py --start 2024-01-01 --end 2026-09-24

Kept: form 8-K (not 8-K/A) with at least one NEWS item below and without Item 2.02 (earnings releases are the event
book's job). A filing is kept only if its company was an S&P 500 member on Jan 1 of the filing year (the Wikipedia
revision membership in data/events/members_2024_2026.csv, built by build_events.py).
Timing: the SEC acceptance time; a trade may use a filing only from the next market open after it.

Writes (refuses to overwrite) data/events/news8k_<start>_<end>.csv:
  cik, ticker, sector, accession, accepted_utc, filed, items, category, primary_url, ex99_url
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import pandas as pd
from build_events import Sec, ex99_url

from app.tools.gateway import _read_env_file

NEWS = {  # item -> category (the first matching category in this order wins)
    "4.02": "distress", "3.01": "distress", "2.04": "distress", "1.03": "distress",
    "2.05": "restructuring", "2.06": "restructuring",
    "1.01": "deal", "2.01": "deal", "1.02": "deal",
    "5.02": "leadership",
}
ORDER = ("distress", "restructuring", "deal", "leadership")


def category(items: str) -> str | None:
    its = {i.strip() for i in items.split(",") if i.strip()}
    if "2.02" in its:
        return None
    cats = {NEWS[i] for i in its if i in NEWS}
    return next((c for c in ORDER if c in cats), None)


async def company_8ks(sec: Sec, cik: int, start: str, end: str) -> list[dict[str, Any]]:
    r = await sec.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    if r is None:
        return []
    sub = r.json()
    blocks = [sub["filings"]["recent"]]
    for f in sub["filings"].get("files", []):
        if f.get("filingTo", "") >= start and f.get("filingFrom", "9999") <= end:
            extra = await sec.get(f"https://data.sec.gov/submissions/{f['name']}")
            if extra is not None:
                blocks.append(extra.json())
    out = []
    for b in blocks:
        for i, form in enumerate(b["form"]):
            items = (b.get("items") or [""] * (i + 1))[i] or ""
            cat = category(items) if form == "8-K" else None
            if cat and start <= b["filingDate"][i] <= end:
                acc = b["accessionNumber"][i]
                out.append({"cik": cik, "accession": acc, "filed": b["filingDate"][i],
                            "accepted_utc": b["acceptanceDateTime"][i][:19], "items": items, "category": cat,
                            "primary_url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/"
                                           f"{b['primaryDocument'][i]}"})
    return out


async def run(args: argparse.Namespace) -> None:
    folder = BACKEND / "data" / "events"
    out_path = folder / f"news8k_{args.start}_{args.end}.csv"
    if out_path.exists():
        raise SystemExit(f"{out_path} exists; delete it to rebuild")
    ua = _read_env_file(BACKEND / ".env").get("SEC_USER_AGENT", "")
    if not ua:
        raise SystemExit("set SEC_USER_AGENT in backend/.env")
    members = pd.read_csv(folder / "members_2024_2026.csv")
    members = members[members["index"] == "sp500"]
    sec = Sec(ua)
    ciks = sorted(set(members["cik"].astype(int)))
    print(f"{len(ciks)} S&P 500 companies (2024-26 members); fetching filing lists", flush=True)
    found: list[dict[str, Any]] = []
    t0 = time.monotonic()
    for i, cik in enumerate(ciks, 1):
        found += await company_8ks(sec, cik, args.start, args.end)
        if i % 100 == 0:
            print(f"  {i}/{len(ciks)} companies, {len(found)} news 8-Ks, {time.monotonic() - t0:.0f}s", flush=True)
    mem = {(int(r.cik), int(r.year)): (r.ticker, r.sector) for r in members.itertuples()}
    kept = [{**ev, "ticker": m[0], "sector": m[1]} for ev in found
            if (m := mem.get((ev["cik"], int(ev["filed"][:4]))))]
    print(f"{len(kept)} from index members; finding press-release exhibits", flush=True)
    sem = asyncio.Semaphore(4)

    async def one(ev: dict[str, Any]) -> None:
        async with sem:
            ev["ex99_url"] = await ex99_url(sec, ev)
    for c in range(0, len(kept), 200):
        await asyncio.gather(*(one(ev) for ev in kept[c:c + 200]))
        print(f"  exhibits {min(c + 200, len(kept))}/{len(kept)}, {time.monotonic() - t0:.0f}s", flush=True)
    await sec.client.aclose()
    out = pd.DataFrame(kept).sort_values(["accepted_utc", "ticker"])
    out.to_csv(out_path, index=False)
    meta = {"built_at": datetime.now(UTC).isoformat(timespec="seconds"), "window": [args.start, args.end],
            "rule": "8-K (not 8-K/A) with a NEWS item and no Item 2.02, S&P 500 member on Jan 1 of the filing year",
            "news_items": NEWS, "events": len(out), "by_category": out["category"].value_counts().to_dict(),
            "with_exhibit": int((out["ex99_url"].fillna("") != "").sum()),
            "sha256": hashlib.sha256(out_path.read_bytes()).hexdigest()}
    out_path.with_suffix(".meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    print(json.dumps({k: meta[k] for k in ("events", "by_category", "with_exhibit")}))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2026-09-24")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
