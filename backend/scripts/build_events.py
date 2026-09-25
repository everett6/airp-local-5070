"""Earnings-event dataset: every 8-K with Item 2.02 (results of operations) from S&P 1500 members, from free sources.

    python scripts/build_events.py --start 2024-01-01 --end 2026-09-24

Point in time:
  - membership: S&P 500 / 400 / 600 constituents from the last Wikipedia revision before Jan 1 of each event's
    year; an event is kept only if its company was a member on that date;
  - timing: the SEC acceptance time of the 8-K (UTC in the submissions API: Apple's 16:30 ET releases show as
    20:30); trades may only use it from the next market open after it.
For each event it records the filing, the acceptance time, and the URL of its press-release exhibit (EX-99.x).

Writes (refuses to overwrite)
  data/events/members_<years>.csv        year, index, ticker, name, sector, cik
  data/events/events_<start>_<end>.csv   cik, ticker, index, sector, accession, accepted_utc, filed, items, ex99_url
  data/events/events_<start>_<end>.meta.json  Wikipedia revisions, counts, companies SEC could not match

SEC fair-access rule is 10 requests/s; this paces itself at 5/s so a second job can share the limit.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import re
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import httpx
import pandas as pd

from app.tools.gateway import _read_env_file

UA_WIKI = "airp-local-5070/0.2 (personal research; +https://github.com/everett6/airp-local-5070)"
LISTS = {"sp500": "List of S&P 500 companies", "sp400": "List of S&P 400 companies",
         "sp600": "List of S&P 600 companies"}
SEC_INTERVAL = 0.2


def members_as_of(title: str, as_of: date) -> tuple[pd.DataFrame, dict[str, Any]]:
    p = {"action": "query", "prop": "revisions", "titles": title, "rvlimit": "1", "rvdir": "older",
         "rvstart": f"{as_of.isoformat()}T00:00:00Z", "rvprop": "ids|timestamp", "format": "json"}
    rev = next(iter(httpx.get("https://en.wikipedia.org/w/api.php", params=p, headers={"User-Agent": UA_WIKI},
                              timeout=30).json()["query"]["pages"].values()))["revisions"][0]
    html = httpx.get("https://en.wikipedia.org/w/index.php", params={"oldid": rev["revid"]},
                     headers={"User-Agent": UA_WIKI}, timeout=30, follow_redirects=True).text
    for t in pd.read_html(io.StringIO(html)):
        cols = {str(c).lower(): c for c in t.columns}
        sym = next((cols[k] for k in ("symbol", "ticker symbol", "ticker") if k in cols), None)
        if sym is None or len(t) < 300:
            continue  # the other table on the page lists index changes
        name = next((cols[k] for k in ("security", "company") if k in cols), sym)
        df = pd.DataFrame({"ticker": t[sym].astype(str).str.strip(), "name": t[name].astype(str),
                           "sector": t[cols["gics sector"]] if "gics sector" in cols else "",
                           "cik": t[cols["cik"]] if "cik" in cols else None})
        return df, {"revision": rev["revid"], "timestamp": rev["timestamp"]}
    raise SystemExit(f"no constituents table in {title} at {as_of}")


class Sec:
    def __init__(self, ua: str) -> None:
        self.client = httpx.AsyncClient(headers={"User-Agent": ua}, timeout=60, follow_redirects=True)
        self.lock = asyncio.Lock()
        self.last = 0.0

    async def get(self, url: str) -> httpx.Response | None:
        for attempt in range(4):
            async with self.lock:
                wait = self.last + SEC_INTERVAL - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                self.last = time.monotonic()
            try:
                r = await self.client.get(url)
            except httpx.HTTPError:
                await asyncio.sleep(2 * (attempt + 1))
                continue
            if r.status_code == 200:
                return r
            if r.status_code in (403, 429, 500, 502, 503):
                await asyncio.sleep(5 * (attempt + 1))
                continue
            return None
        return None


async def company_events(sec: Sec, cik: int, start: str, end: str) -> list[dict[str, Any]]:
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
            if form == "8-K" and "2.02" in items.split(",") and start <= b["filingDate"][i] <= end:
                out.append({"cik": cik, "accession": b["accessionNumber"][i], "filed": b["filingDate"][i],
                            "accepted_utc": b["acceptanceDateTime"][i][:19], "items": items,
                            "primary": b["primaryDocument"][i]})
    return out


async def ex99_url(sec: Sec, ev: dict[str, Any]) -> str:
    acc = ev["accession"]
    idx = f"https://www.sec.gov/Archives/edgar/data/{ev['cik']}/{acc.replace('-', '')}/{acc}-index.htm"
    r = await sec.get(idx)
    if r is None:
        return ""
    # the document table: description, link, type; take the first EX-99 exhibit (the press release)
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", r.text, flags=re.DOTALL | re.IGNORECASE)
    for row in rows:
        if re.search(r">\s*EX-99(\.1)?\s*<", row, flags=re.IGNORECASE) or re.search(r">\s*EX-99\.01\s*<", row, flags=re.IGNORECASE):
            m = re.search(r'href="(/Archives/edgar/data/[^"]+\.(?:htm|html|txt))"', row, flags=re.IGNORECASE)
            if m:
                return "https://www.sec.gov" + m.group(1).replace("/ix?doc=", "")
    for row in rows:  # EX-99.2 etc. if there is no 99.1
        if re.search(r">\s*EX-99", row, flags=re.IGNORECASE):
            m = re.search(r'href="(/Archives/edgar/data/[^"]+\.(?:htm|html|txt))"', row, flags=re.IGNORECASE)
            if m:
                return "https://www.sec.gov" + m.group(1)
    return ""


async def run(args: argparse.Namespace) -> None:
    folder = BACKEND / "data" / "events"
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{args.start}_{args.end}"
    ev_path, meta_path = folder / f"events_{stem}.csv", folder / f"events_{stem}.meta.json"
    if ev_path.exists():
        raise SystemExit(f"{ev_path} exists; delete it to rebuild")
    years = list(range(int(args.start[:4]), int(args.end[:4]) + 1))
    env = _read_env_file(BACKEND / ".env")
    ua = env.get("SEC_USER_AGENT", "")
    if not ua:
        raise SystemExit("set SEC_USER_AGENT in backend/.env")
    sec = Sec(ua)
    tick = (await sec.get("https://www.sec.gov/files/company_tickers.json"))
    assert tick is not None
    by_ticker = {v["ticker"]: int(v["cik_str"]) for v in tick.json().values()}

    rows, revisions = [], {}
    for y in years:
        for idx, title in LISTS.items():
            df, rev = members_as_of(title, date(y, 1, 1))
            revisions[f"{idx}_{y}"] = rev
            df["year"], df["index"] = y, idx
            rows.append(df)
            print(y, idx, len(df), "members", rev["timestamp"], flush=True)
    members = pd.concat(rows, ignore_index=True)

    def cik_of(r: pd.Series) -> int | None:
        v = r["cik"]
        if pd.notna(v) and str(v).strip().isdigit():
            return int(str(v).strip())
        t = r["ticker"]
        return by_ticker.get(t) or by_ticker.get(t.replace(".", "-")) or by_ticker.get(t.replace(".", ""))
    members["cik"] = members.apply(cik_of, axis=1)
    unmatched = sorted(set(members.loc[members["cik"].isna(), "ticker"]))
    members = members.dropna(subset=["cik"])
    members["cik"] = members["cik"].astype(int)
    members.to_csv(folder / f"members_{years[0]}_{years[-1]}.csv", index=False)

    ciks = sorted(set(members["cik"]))
    print(f"{len(ciks)} companies; fetching filing lists", flush=True)
    events: list[dict[str, Any]] = []
    t0 = time.monotonic()
    for i, cik in enumerate(ciks, 1):
        events += await company_events(sec, cik, args.start, args.end)
        if i % 100 == 0:
            print(f"  {i}/{len(ciks)} companies, {len(events)} earnings 8-Ks, {time.monotonic() - t0:.0f}s", flush=True)
    # keep an event only if its company was in the index on Jan 1 of the event's year
    mem = {(int(r.cik), int(r.year)): (r.ticker, r.index, r.sector) for r in members.itertuples()}
    kept = []
    for ev in events:
        m = mem.get((ev["cik"], int(ev["filed"][:4])))
        if m:
            kept.append({**ev, "ticker": m[0], "index": m[1], "sector": m[2]})
    print(f"{len(kept)} events from index members; finding press-release exhibits", flush=True)
    sem = asyncio.Semaphore(4)

    async def one(ev: dict[str, Any]) -> None:
        async with sem:
            ev["ex99_url"] = await ex99_url(sec, ev)
    done = 0
    for chunk in range(0, len(kept), 200):
        await asyncio.gather(*(one(ev) for ev in kept[chunk:chunk + 200]))
        done += len(kept[chunk:chunk + 200])
        print(f"  exhibits {done}/{len(kept)}, {time.monotonic() - t0:.0f}s", flush=True)
    await sec.client.aclose()
    out = pd.DataFrame(kept).sort_values(["accepted_utc", "ticker"])
    out.to_csv(ev_path, index=False)
    meta = {"built_at": datetime.now(UTC).isoformat(timespec="seconds"), "window": [args.start, args.end],
            "rule": "8-K with Item 2.02, company in S&P 500/400/600 on Jan 1 of the filing year (Wikipedia revision)",
            "wikipedia_revisions": revisions, "companies": len(ciks), "events": len(out),
            "events_without_exhibit": int((out["ex99_url"] == "").sum()), "tickers_not_matched_to_cik": unmatched,
            "sha256": hashlib.sha256(ev_path.read_bytes()).hexdigest()}
    meta_path.write_text(json.dumps(meta, indent=1) + "\n")
    print(json.dumps({k: meta[k] for k in ("companies", "events", "events_without_exhibit")}))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2026-09-24")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
