"""
Point-in-time fundamentals from SEC EDGAR (public-domain data).

Download (network, once):
    python -m app.data_ingestion.edgar --universe data/universe_2025-06-02_top100.csv

  - ticker -> CIK from sec.gov/files/company_tickers.json
  - filing index from data.sec.gov/submissions (plus the paged older files that overlap the window)
  - XBRL facts per concept from data.sec.gov/api/xbrl/companyconcept (EPS, revenue)
  Requests are paced under SEC's 10/second limit and carry the contact User-Agent from
  backend/.env (SEC_USER_AGENT), which is never committed.
  The compact result is written to data/edgar/fundamentals.json.

Point-in-time rule (conservative): a filing or XBRL value is known at cutoff c only if it was
FILED ON A DATE BEFORE c. EDGAR dates are calendar days, and a filing accepted on c after the
close would otherwise leak. Values later restated keep their original number until the
restating filing's own filing date. `PITFundamentals.features` re-checks every date it uses
against the active sandbox clock, so a bug aborts the run instead of leaking.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import statistics
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.sandbox.clock import enforce_point_in_time

BACKEND = Path(__file__).resolve().parents[2]
OUT = BACKEND / "data" / "edgar" / "fundamentals.json"

EPS_TAGS = ["EarningsPerShareDiluted", "EarningsPerShareBasic"]
REVENUE_TAGS = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
                "RevenuesNetOfInterestExpense", "RevenueFromContractWithCustomerIncludingAssessedTax"]
EARNINGS_FALLBACK = "NetIncomeLoss"  # multi-class companies (V, BRK-B) don't tag a single EPS; SUE is scale-free
RECENT = "2025-01-01"
FORMS = {"10-Q", "10-K", "10-Q/A", "10-K/A", "8-K", "8-K/A"}
HISTORY_FROM = "2021-01-01"  # SUE needs ~3 years of quarterly history before the 2025 window

Fact = tuple[str, str, float, str]  # (start, end, value, filed) as ISO dates


# ---------------- download ----------------

async def download(tickers: list[str], user_agent: str, out: Path = OUT) -> dict[str, Any]:
    from app.tools.netguard import FetchError, SafeFetcher

    fetcher = SafeFetcher(user_agent, timeout_s=20, max_bytes=40_000_000, cache_ttl_s=0)
    hdr = {"User-Agent": user_agent}

    async def get_json(url: str) -> Any | None:
        for attempt in range(4):
            try:
                r = await fetcher.fetch(url, headers=hdr, use_cache=False)
            except FetchError:
                if attempt == 3:
                    raise
                await asyncio.sleep(2 ** attempt)
                continue
            if r.status == 404:
                return None
            if r.status == 200:
                return json.loads(r.text)
            await asyncio.sleep(2 ** attempt)  # 429/5xx: back off
        raise FetchError(f"giving up on {url}")

    try:
        mapping = await get_json("https://www.sec.gov/files/company_tickers.json")
        cik_of = {v["ticker"].upper().replace(".", "-"): int(v["cik_str"]) for v in mapping.values()}
        sem = asyncio.Semaphore(4)  # the fetcher also paces sec.gov hosts to ~8 requests/second

        async def one(ticker: str) -> tuple[str, dict[str, Any]]:
            cik = cik_of.get(ticker.upper())
            if cik is None:
                return ticker, {"error": "not in SEC company tickers"}
            async with sem:
                sub = await get_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
                blocks = [sub["filings"]["recent"]] if sub else []
                for page in (sub or {}).get("filings", {}).get("files", []):
                    if page.get("filingTo", "9999") >= HISTORY_FROM:
                        extra = await get_json(f"https://data.sec.gov/submissions/{page['name']}")
                        if extra:
                            blocks.append(extra)
                filings = sorted({(b["form"][i], b["filingDate"][i], b.get("items", [""] * len(b["form"]))[i] or "")
                                  for b in blocks for i in range(len(b["form"]))
                                  if b["form"][i] in FORMS and b["filingDate"][i] >= HISTORY_FROM},
                                 key=lambda x: x[1])
                concepts: dict[str, Any] = {}
                company_facts: dict[str, Any] | None = None

                async def tag_facts(tag: str, unit: str) -> list[Fact]:
                    nonlocal company_facts
                    data = await get_json(f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/us-gaap/{tag}.json")
                    facts = _facts(data, unit) if data else []
                    if facts:
                        return facts
                    # the concept API sometimes returns a tag with no values (seen for KO) while companyfacts has them
                    if company_facts is None:
                        company_facts = await get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json") or {}
                    g = company_facts.get("facts", {}).get("us-gaap", {})
                    return _facts(g[tag], unit) if tag in g else []

                for group, chain in (("eps", [*((tg, "USD/shares") for tg in EPS_TAGS), (EARNINGS_FALLBACK, "USD")]),
                                     ("revenue", [(tg, "USD") for tg in REVENUE_TAGS])):
                    best: tuple[str, list[Fact]] | None = None
                    for tag, unit in chain:
                        facts = await tag_facts(tag, unit)
                        if not facts:
                            continue
                        if group == "eps" and max(f[1] for f in facts) >= RECENT:
                            best = (tag, facts)  # EPS: first tag in priority order that is still reported
                            break
                        if best is None or max(f[1] for f in facts) > max(f[1] for f in best[1]):
                            best = (tag, facts)  # revenue: the tag with the most recent data
                    concepts[group] = {"tag": best[0], "facts": best[1]} if best else {"tag": None, "facts": []}
            return ticker, {"cik": cik, "name": (sub or {}).get("name", ""), "filings": filings, **concepts}

        results = dict(await asyncio.gather(*(one(t) for t in tickers)))
    finally:
        await fetcher.aclose()
    doc = {"fetched_at": datetime.now(UTC).isoformat(timespec="seconds"), "history_from": HISTORY_FROM,
           "source": "SEC EDGAR submissions and XBRL companyconcept APIs (public domain)", "companies": results}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, separators=(",", ":")) + "\n")
    return doc


def _facts(data: dict[str, Any], unit: str) -> list[Fact]:
    rows = []
    for f in data.get("units", {}).get(unit, []):
        if "start" in f and "end" in f and "filed" in f and f.get("end", "") >= HISTORY_FROM[:4] + "-01-01":
            try:
                v = float(f["val"])
            except (TypeError, ValueError):
                continue
            if math.isfinite(v):
                rows.append((f["start"], f["end"], v, f["filed"]))
    return sorted(set(rows), key=lambda r: (r[1], r[3]))


# ---------------- point-in-time quarterly series ----------------

@dataclass(frozen=True)
class Quarter:
    end: date
    value: float
    first_filed: date  # when this quarter's number first became public (drives "days since report")


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def quarters_as_of(facts: list[Fact], cutoff: date) -> list[Quarter]:
    """Quarterly values as an investor could know them before `cutoff`, oldest first.
    Latest-filed value per period (restatements count only once filed); Q4 derived as FY minus
    the three quarters inside it when a company only reports the annual figure."""
    c = cutoff.isoformat()
    known = [f for f in facts if f[3] < c]
    latest: dict[tuple[str, str], Fact] = {}
    first: dict[tuple[str, str], str] = {}
    for f in known:
        k = (f[0], f[1])
        if k not in latest or f[3] >= latest[k][3]:
            latest[k] = f
        first[k] = min(first.get(k, f[3]), f[3])
    quarterly = {k: v for k, v in latest.items() if 75 <= _days(k[0], k[1]) <= 105}
    annual = {k: v for k, v in latest.items() if 340 <= _days(k[0], k[1]) <= 380}
    for (s, e), fy in annual.items():
        inside = sorted((k, v) for k, v in quarterly.items() if k[0] >= s and k[1] <= e)
        has_q4 = any(abs(_days(k[1], e)) <= 10 for k, _ in inside)
        if len(inside) == 3 and not has_q4:
            q3_end = inside[-1][0][1]
            k4 = ((date.fromisoformat(q3_end) + timedelta(days=1)).isoformat(), e)
            quarterly[k4] = (k4[0], e, fy[2] - sum(v[2] for _, v in inside), fy[3])
            first[k4] = first[(s, e)]
    out: list[Quarter] = []
    for (_, e), v in sorted(quarterly.items(), key=lambda kv: (kv[0][1], kv[1][3])):
        q = Quarter(date.fromisoformat(e), v[2], date.fromisoformat(first[(v[0], e)]))
        if out and (q.end - out[-1].end).days <= 20:  # same quarter reported with slightly different period ends
            out[-1] = q if q.first_filed >= out[-1].first_filed else out[-1]
            continue
        out.append(q)
    return out


def _year_ago(qs: list[Quarter], i: int) -> Quarter | None:
    target = qs[i].end - timedelta(days=365)
    for j in range(i - 1, -1, -1):
        if abs((qs[j].end - target).days) <= 25:
            return qs[j]
        if qs[j].end < target - timedelta(days=40):
            break
    return None


def sue_series(qs: list[Quarter], min_history: int = 4) -> list[float | None]:
    """Standardized unexpected earnings (seasonal random walk): (E_q - E_{q-4}) / sd of the prior 8 such changes."""
    changes: list[float | None] = []
    for i in range(len(qs)):
        prev = _year_ago(qs, i)
        changes.append(qs[i].value - prev.value if prev else None)
    sues: list[float | None] = []
    for i, ch in enumerate(changes):
        hist = [c for c in changes[max(0, i - 8):i] if c is not None]
        if ch is None or len(hist) < min_history:
            sues.append(None)
            continue
        sd = statistics.pstdev(hist)
        sues.append(max(-5.0, min(5.0, ch / sd)) if sd > 1e-9 else None)
    return sues


def _growth(qs: list[Quarter]) -> float | None:
    if not qs:
        return None
    prev = _year_ago(qs, len(qs) - 1)
    if prev is None or abs(prev.value) < 1e-9:
        return None
    return max(-1.0, min(3.0, (qs[-1].value - prev.value) / abs(prev.value)))


FUND_KEYS = ["sue_1", "sue_2", "sue_3", "sue_4", "eps_yoy", "rev_yoy", "days_since_report",
             "days_since_earnings_8k", "n_8k_30d", "earnings_expected", "fund_ok"]


class PITFundamentals:
    def __init__(self, doc: dict[str, Any]) -> None:
        self.companies: dict[str, Any] = doc["companies"]

    @classmethod
    def load(cls, path: Path = OUT) -> PITFundamentals:
        return cls(json.loads(path.read_text()))

    def features(self, ticker: str, cutoff: date, horizon_days: int = 5) -> dict[str, float]:
        comp = self.companies.get(ticker) or {}
        c = cutoff.isoformat()
        eps_facts = [tuple(f) for f in (comp.get("eps") or {}).get("facts", [])]
        rev_facts = [tuple(f) for f in (comp.get("revenue") or {}).get("facts", [])]
        eps_q = quarters_as_of(eps_facts, cutoff)  # type: ignore[arg-type]
        rev_q = quarters_as_of(rev_facts, cutoff)  # type: ignore[arg-type]
        filings = [f for f in comp.get("filings", []) if f[1] < c]
        for q in (eps_q[-1:] + rev_q[-1:]):
            enforce_point_in_time(_eod(q.first_filed), source=f"edgar:{ticker}")
        for f in filings[-1:]:
            enforce_point_in_time(_eod(date.fromisoformat(f[1])), source=f"edgar:{ticker}")
        sues = [s for s in sue_series(eps_q)][-4:][::-1]
        sues += [None] * (4 - len(sues))
        earn8k = [f[1] for f in filings if f[0].startswith("8-K") and "2.02" in f[2]]
        reports = [f[1] for f in filings if f[0].split("/")[0] in ("10-Q", "10-K")]
        last_year = cutoff - timedelta(days=364)
        window_end = last_year + timedelta(days=int(horizon_days * 7 / 5) + 3)
        expected = any(last_year.isoformat() < d <= window_end.isoformat()
                       for d in (f[1] for f in comp.get("filings", []) if f[0].startswith("8-K") and "2.02" in f[2]))
        cap = 180.0
        out = {
            "sue_1": sues[0] or 0.0, "sue_2": sues[1] or 0.0, "sue_3": sues[2] or 0.0, "sue_4": sues[3] or 0.0,
            "eps_yoy": _growth(eps_q) or 0.0, "rev_yoy": _growth(rev_q) or 0.0,
            "days_since_report": float(min(cap, (cutoff - eps_q[-1].first_filed).days)) if eps_q else cap,
            "days_since_earnings_8k": float(min(cap, _days(earn8k[-1], c))) if earn8k else cap,
            "n_8k_30d": float(sum(1 for f in filings if f[0].startswith("8-K")
                                  and f[1] >= (cutoff - timedelta(days=30)).isoformat())),
            "earnings_expected": 1.0 if expected else 0.0,
            "fund_ok": 1.0 if (eps_q and sues[0] is not None) else 0.0,
        }
        return {k: round(v, 4) for k, v in out.items()}


def _eod(d: date) -> datetime:
    # a value filed on day d is treated as known from the end of that day (the cutoff clock is 00:00 UTC of c)
    return datetime(d.year, d.month, d.day, tzinfo=UTC) + timedelta(days=1)


def digest_text(f: dict[str, float]) -> str:
    """Numbers-only summary for the jailed agent: no names, no dates, no free text from filings."""
    if not f.get("fund_ok"):
        return "Fundamentals: not enough reported history for this asset."
    def pct(x: float) -> str:
        return f"{x * 100:+.1f}%"
    return (
        "Fundamentals as reported before today (SEC filings, numbers only): "
        f"latest quarter earnings vs the same quarter a year earlier {pct(f['eps_yoy'])}, "
        f"revenue {pct(f['rev_yoy'])}; "
        f"standardized earnings surprise for the last four quarters (newest first) "
        f"{f['sue_1']:+.2f}, {f['sue_2']:+.2f}, {f['sue_3']:+.2f}, {f['sue_4']:+.2f}; "
        f"days since the latest quarterly report {f['days_since_report']:.0f}, since the latest earnings release "
        f"{f['days_since_earnings_8k']:.0f}; 8-K filings in the last 30 days {f['n_8k_30d']:.0f}; "
        f"an earnings release {'is' if f['earnings_expected'] else 'is not'} expected within the forecast horizon "
        "(based on last year's reporting calendar)."
    )


def sec_user_agent() -> str:
    import os

    from app.tools.gateway import _read_env_file

    ua = os.environ.get("SEC_USER_AGENT") or _read_env_file(BACKEND / ".env").get("SEC_USER_AGENT", "")
    if not ua:
        raise SystemExit("set SEC_USER_AGENT (name and email) in backend/.env; SEC requires a contact")
    return ua


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--universe", required=True, help="CSV with a 'ticker' column, under backend/")
    args = ap.parse_args()
    with (BACKEND / args.universe).open() as fh:
        tickers = [r["ticker"] for r in csv.DictReader(fh)]
    doc = asyncio.run(download(tickers, sec_user_agent()))
    comps = doc["companies"]
    bad = {t: v["error"] for t, v in comps.items() if "error" in v}
    no_eps = [t for t, v in comps.items() if "error" not in v and not v["eps"]["facts"]]
    no_rev = [t for t, v in comps.items() if "error" not in v and not v["revenue"]["facts"]]
    print(json.dumps({"companies": len(comps), "errors": bad, "no_eps": no_eps, "no_revenue": no_rev,
                      "written": str(OUT.relative_to(BACKEND))}, indent=1))


if __name__ == "__main__":
    main()
