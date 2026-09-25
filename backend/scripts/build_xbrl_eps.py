"""Quarterly diluted EPS and revenue as FIRST reported to the SEC (XBRL), for S&P 1500 members 2010-2026. No LLM.

    python scripts/build_xbrl_eps.py members          # Jan-1 S&P 500/400/600 membership 2010-2026 (Wikipedia revisions)
    python scripts/build_xbrl_eps.py facts            # SEC XBRL company-concept API, one request per company and tag

Point in time: each quarter's value is the one in the FIRST filing that reported it (later filings restate
comparatives; those restated numbers are ignored), and it is known from that filing's date (`filed`, a date without a
time, so trades use the next day's open). Q4 is not filed on its own: it is the 10-K's annual value minus the three
quarters, known at the 10-K's date (flagged `derived`). The year-earlier quarter is the first-reported value for the
quarter ending ~1 year before as restated in
the same filing (so splits don't distort it), else as first reported.

Outputs data/xbrl/members_2010_2026.csv and data/xbrl/eps_quarterly.csv (cik, end, fy, fp, form, filed, accn, eps,
eps_prior, rev, rev_prior, derived). Free SEC API, paced 0.2 s per request with the SEC_USER_AGENT from backend/.env.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import pandas as pd
from build_events import LISTS, Sec, members_as_of

from app.tools.gateway import _read_env_file

OUT = BACKEND / "data" / "xbrl"
EPS_TAGS = ("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted")
REV_TAGS = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet")


def build_members(years: range) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows, revs = [], {}
    for y in years:
        for idx, title in LISTS.items():
            try:
                df, rev = members_as_of(title, date(y, 1, 1))
            except (SystemExit, KeyError, StopIteration, ValueError) as e:  # early 400/600 pages have no full table
                print(y, idx, "skipped:", e, flush=True)
                continue
            df["year"], df["index"] = y, idx
            rows.append(df)
            revs[f"{idx}_{y}"] = rev
            print(y, idx, len(df), rev["timestamp"], flush=True)
    m = pd.concat(rows, ignore_index=True)
    m.to_csv(OUT / "members_raw.csv", index=False)
    (OUT / "members_raw.meta.json").write_text(json.dumps(revs, indent=1) + "\n")


def facts_for(unit_rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str], float]]:
    """end date -> first-reported quarterly value (3-month duration), plus Q4 derived from the 10-K; and every
    quarterly value by (filing, end), so a filing's own year-earlier comparative (restated for splits) can be used."""
    q: dict[str, dict[str, Any]] = {}
    comps: dict[tuple[str, str], float] = {}
    annual: dict[str, dict[str, Any]] = {}
    for r in sorted(unit_rows, key=lambda r: (r.get("filed", ""), r.get("accn", ""))):
        if "start" not in r or r.get("form") not in ("10-Q", "10-K", "10-Q/A", "10-K/A", "20-F", "40-F"):
            continue
        days = (date.fromisoformat(r["end"]) - date.fromisoformat(r["start"])).days
        rec = {"end": r["end"], "start": r["start"], "val": float(r["val"]), "filed": r["filed"], "accn": r["accn"],
               "form": r["form"], "fy": r.get("fy"), "fp": r.get("fp")}
        if 80 <= days <= 100:
            comps.setdefault((r["accn"], r["end"]), rec["val"])
        if 80 <= days <= 100 and r["end"] not in q:
            q[r["end"]] = rec
        elif 350 <= days <= 380 and r["end"] not in annual:
            annual[r["end"]] = rec
    for end, a in annual.items():
        if end in q:
            continue
        s, e = date.fromisoformat(a["start"]), date.fromisoformat(end)
        inside = [v for k, v in q.items() if s < date.fromisoformat(k) < e and date.fromisoformat(v["start"]) >= s]
        if len(inside) == 3:
            q[end] = {**a, "val": a["val"] - sum(v["val"] for v in inside), "derived": True,
                      "filed": max(a["filed"], *(v["filed"] for v in inside))}
    return q, comps


def prior_of(q: dict[str, dict[str, Any]], end: str, comps: dict[tuple[str, str], float] | None = None,
             accn: str = "") -> dict[str, Any] | None:
    """Year-earlier quarter: the value restated in the same filing (`accn`) when it has one (splits), else the
    first-reported value."""
    e = date.fromisoformat(end)
    best = None
    for k, v in q.items():
        gap = (e - date.fromisoformat(k)).days
        if 350 <= gap <= 380 and (best is None or abs(gap - 365) < abs(best[0] - 365)):
            best = (gap, v)
    if best is None:
        return None
    if comps and (accn, best[1]["end"]) in comps:
        return {**best[1], "val": comps[(accn, best[1]["end"])], "restated_in_filing": True}
    return best[1]


async def concept(sec: Sec, cik: int, tag: str, unit: str) -> list[dict[str, Any]]:
    r = await sec.get(f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/us-gaap/{tag}.json")
    if r is None:
        return []
    return list(r.json().get("units", {}).get(unit, []))


async def build_facts() -> None:
    ua = _read_env_file(BACKEND / ".env").get("SEC_USER_AGENT", "")
    if not ua:
        raise SystemExit("set SEC_USER_AGENT in backend/.env")
    sec = Sec(ua)
    raw = pd.read_csv(OUT / "members_raw.csv").dropna(subset=["ticker"])
    # Wikipedia's S&P 600 page listed ~1,000 names in 2019-2021 (stale removals left in): an index-year whose list is
    # over 10% longer than the index is not a reliable membership record, so it is left out rather than guessed at
    size = raw.groupby(["year", "index"])["ticker"].transform("nunique")
    raw = raw[size <= 1.1 * raw["index"].map({"sp500": 505, "sp400": 400, "sp600": 600})]
    tick = await sec.get("https://www.sec.gov/files/company_tickers.json")
    assert tick is not None
    by_ticker = {v["ticker"]: int(v["cik_str"]) for v in tick.json().values()}

    def cik_of(r: pd.Series) -> int | None:
        v = r["cik"]
        if pd.notna(v) and str(v).strip().split(".")[0].isdigit():
            return int(str(v).strip().split(".")[0])
        t = str(r["ticker"])
        return by_ticker.get(t) or by_ticker.get(t.replace(".", "-")) or by_ticker.get(t.replace(".", ""))
    raw["cik"] = raw.apply(cik_of, axis=1)
    unmatched = sorted(set(raw.loc[raw["cik"].isna(), "ticker"]))
    members = raw.dropna(subset=["cik"]).astype({"cik": int})
    members.to_csv(OUT / "members_2010_2026.csv", index=False)
    ciks = sorted(set(members["cik"]))
    print(f"{len(ciks)} companies ({len(unmatched)} tickers without a CIK: delisted, recorded)", flush=True)

    part = OUT / "eps_quarterly.partial.jsonl"
    done = {json.loads(x)["cik"] for x in part.read_text().splitlines()} if part.exists() else set()
    t0 = time.monotonic()

    async def one(cik: int) -> list[dict[str, Any]]:
        eps: dict[str, dict[str, Any]] = {}
        ecomp: dict[tuple[str, str], float] = {}
        for tag in EPS_TAGS:
            eps, ecomp = facts_for(await concept(sec, cik, tag, "USD/shares"))
            if eps:
                break
        rev: dict[str, dict[str, Any]] = {}
        for tag in REV_TAGS:  # a company may switch tags (ASC 606 in 2018): merge, first-reported wins
            for k, v in facts_for(await concept(sec, cik, tag, "USD"))[0].items():
                rev.setdefault(k, v)
        rows = []
        for end, v in eps.items():
            p = prior_of(eps, end, ecomp, v["accn"])
            r = rev.get(end)
            rp = prior_of(rev, end) if r else None
            rows.append({"cik": cik, "end": end, "fy": v["fy"], "fp": v["fp"], "form": v["form"],
                         "filed": v["filed"], "accn": v["accn"], "eps": v["val"],
                         "eps_prior": p["val"] if p else None, "eps_prior_filed": p["filed"] if p else None,
                         "prior_restated": bool(p and p.get("restated_in_filing")),
                         "rev": r["val"] / 1e6 if r else None, "rev_prior": rp["val"] / 1e6 if rp else None,
                         "derived": bool(v.get("derived"))})
        return rows

    todo = [c for c in ciks if c not in done]
    queue: asyncio.Queue[int] = asyncio.Queue()
    for c in todo:
        queue.put_nowait(c)
    with part.open("a") as f:
        async def worker() -> None:  # 12 companies in flight; Sec still paces every request to 0.2 s
            while not queue.empty():
                cik = queue.get_nowait()
                f.write(json.dumps({"cik": cik, "rows": await one(cik)}) + "\n")
                f.flush()
                n = len(todo) - queue.qsize()
                if n % 100 == 0:
                    print(f"  {n + len(done)}/{len(ciks)} {time.monotonic() - t0:.0f}s", flush=True)
        await asyncio.gather(*(worker() for _ in range(12)))
    rows = [r for x in part.read_text().splitlines() for r in json.loads(x)["rows"]]
    df = pd.DataFrame(rows).sort_values(["cik", "end"])
    df.to_csv(OUT / "eps_quarterly.csv", index=False)
    (OUT / "eps_quarterly.meta.json").write_text(json.dumps(
        {"companies": len(ciks), "quarters": len(df), "unmatched_tickers": unmatched, "eps_tags": EPS_TAGS,
         "revenue_tags": REV_TAGS, "source": "https://data.sec.gov/api/xbrl/companyconcept"}, indent=1) + "\n")
    print(f"{len(df)} company-quarters", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("members", "facts"))
    ap.add_argument("--start-year", type=int, default=2010)
    ap.add_argument("--end-year", type=int, default=2026)
    args = ap.parse_args()
    if args.cmd == "members":
        build_members(range(args.start_year, args.end_year + 1))
    else:
        asyncio.run(build_facts())


if __name__ == "__main__":
    main()
