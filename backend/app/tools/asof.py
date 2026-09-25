"""
As-of web tools: internet lookups for a backtest, showing only what had been published by the decision time.

A normal web search run today already knows what happened after any past date, so the plain web tools are
refused in backtests. These tools go to free sources that keep dated versions, and each one checks the date
before anything reaches the model:

  wiki_as_of         a Wikipedia article as its last revision before the decision time showed it
  wiki_search        article titles, keeping only articles that already existed at the decision time
  sec_filings_as_of  a company's SEC filings accepted before the decision time (EDGAR, free)
  filing_documents   the documents inside one of those filings (e.g. the EX-99.1 earnings press release)
  read_filing        the text of a filing document; its acceptance time is re-checked on EDGAR every time
  news_as_of         the company's news page (Reuters, MarketWatch, CNBC, Yahoo, Nasdaq) as the Internet Archive
                     captured it in the 45 days before the decision time
  archived_page      any web page, as the Internet Archive captured it before the decision time
  price_history_as_of  daily closes up to the decision time, from the local point-in-time price file

Every date check is conservative: SEC acceptance times are Eastern time, so 5 hours are added before
comparing. Anything dated after the decision time is refused, never truncated or summarised.

What these tools can NOT stop: the language model's own memory. qwen3 was trained on web text up to about
2024, so for earlier decision dates it may simply remember what happened next. Results must be read split
at the model's training cutoff (see docs/LONG_HISTORY.md).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlsplit

from app.tools.extract import html_to_text
from app.tools.netguard import FetchError

if TYPE_CHECKING:
    from app.tools.gateway import ToolGateway

WIKI_API = "https://en.wikipedia.org/w/api.php"
CDX = "https://web.archive.org/cdx/search/cdx"
SEC_TZ_MARGIN = timedelta(hours=5)  # EDGAR acceptance times are US Eastern; +5 h is never early
ARCHIVE_TS = re.compile(r"^https?://web\.archive\.org/web/(\d{14})")
SEC_ARCHIVE = re.compile(r"^https://www\.sec\.gov/Archives/edgar/data/(\d+)/(\d{18})/[^?#]+$")
NEWS_PAGES = (  # (source, URL template, years it was archived); tried in order, first capture in the window wins
    ("marketwatch", "www.marketwatch.com/investing/stock/{t}", 2009, 2100),
    ("reuters", "www.reuters.com/finance/stocks/companyNews?symbol={T}.O", 2009, 2020),
    ("reuters", "www.reuters.com/finance/stocks/companyNews?symbol={T}.N", 2009, 2020),
    ("cnbc", "www.cnbc.com/quotes/{T}", 2020, 2100),
    ("yahoo", "finance.yahoo.com/quote/{T}", 2017, 2100),
    ("yahoo", "finance.yahoo.com/q?s={T}", 2009, 2017),
    # nasdaq.com quote pages were dropped 2026-09-24: 0 captures found in 34 tries, 4 s of rate limit each
)

PriceLookup = Callable[[str, datetime, int], list[tuple[str, float]]]


def _as_of(gw: ToolGateway) -> datetime:
    if gw.as_of is None:
        raise FetchError("as-of tools need a decision time")
    return gw.as_of


def _stamp(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y%m%d%H%M%S")


def _parse_wiki_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def sec_accepted_utc(raw: str) -> datetime:
    """EDGAR acceptance time (Eastern, sometimes labelled Z, or 14 digits) -> a UTC time that is never too early."""
    raw = raw.strip()
    text = raw if not raw.isdigit() else f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}T{raw[8:10]}:{raw[10:12]}:{raw[12:14]}"
    wall = datetime.fromisoformat(text.replace("Z", "").split(".")[0] + "+00:00")  # Eastern wall clock, read as UTC
    return wall + SEC_TZ_MARGIN


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise FetchError("source returned malformed data") from e


# ---------------- Wikipedia ----------------

async def _wiki_revision(gw: ToolGateway, title: str, as_of: datetime) -> dict[str, Any] | None:
    r = await gw.fetcher.fetch(f"{WIKI_API}?action=query&format=json&prop=revisions&rvlimit=1&rvdir=older"
                               f"&rvprop=ids%7Ctimestamp&redirects=1&rvstart={_stamp(as_of)}&titles={quote(title)}")
    pages = _json(r.text).get("query", {}).get("pages", {})
    page: dict[str, Any] = next(iter(pages.values()), {})
    revs = page.get("revisions")
    if not revs:
        return None
    rev = revs[0]
    if _parse_wiki_ts(rev["timestamp"]) > as_of:  # never trust the API's ordering alone
        raise FetchError("revision is newer than the decision time")
    return {"title": page.get("title", title), "revid": rev["revid"], "timestamp": rev["timestamp"]}


async def wiki_as_of(gw: ToolGateway, a: dict[str, Any]) -> Any:
    as_of = _as_of(gw)
    rev = await _wiki_revision(gw, a["title"], as_of)
    if rev is None:
        raise FetchError(f"no Wikipedia article {a['title']!r} existed at the decision time")
    r = await gw.fetcher.fetch(f"{WIKI_API}?action=parse&format=json&prop=text&disablelimitreport=1"
                               f"&oldid={rev['revid']}", max_bytes=6_000_000)
    html = _json(r.text).get("parse", {}).get("text", {}).get("*", "")
    page = html_to_text(html, a["max_chars"])
    return {"title": rev["title"], "revision_time": rev["timestamp"],
            "url": f"https://en.wikipedia.org/w/index.php?oldid={rev['revid']}", "text": page["text"]}


async def wiki_search(gw: ToolGateway, a: dict[str, Any]) -> Any:
    """Search today's index, then keep only titles whose article already existed at the decision time.
    Titles only: today's snippets would describe the article as it reads now."""
    as_of = _as_of(gw)
    r = await gw.fetcher.fetch(f"{WIKI_API}?action=query&format=json&list=search&srlimit=10&srprop="
                               f"&srsearch={quote(a['query'])}")
    titles = [h["title"] for h in _json(r.text).get("query", {}).get("search", [])]
    kept = []
    for t in titles:
        rev = await _wiki_revision(gw, t, as_of)
        if rev is not None:
            kept.append(rev["title"])
        if len(kept) >= a["limit"]:
            break
    return {"query": a["query"], "titles": kept, "note": "read one with wiki_as_of"}


# ---------------- SEC EDGAR ----------------

async def _cik(gw: ToolGateway, ticker: str) -> int:
    hdr = {"User-Agent": gw.sec_user_agent}
    r = await gw.fetcher.fetch("https://www.sec.gov/files/company_tickers.json", headers=hdr, max_bytes=5_000_000)
    wanted = {ticker, ticker.replace("-", "."), ticker.replace("-", "")}
    cik = next((int(v["cik_str"]) for v in _json(r.text).values() if v["ticker"] in wanted), None)
    if cik is None:
        raise FetchError(f"{ticker} not found in SEC company tickers (delisted or renamed companies may be missing)")
    return cik


async def sec_filings_as_of(gw: ToolGateway, a: dict[str, Any]) -> Any:
    if not gw.sec_user_agent:
        raise FetchError("SEC access needs SEC_USER_AGENT (name and email) in backend/.env")
    as_of = _as_of(gw)
    t = a["ticker"].strip().upper()
    cik = await _cik(gw, t)
    hdr = {"User-Agent": gw.sec_user_agent}
    sub = _json((await gw.fetcher.fetch(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", headers=hdr,
                                        max_bytes=8_000_000)).text)
    blocks = [sub["filings"]["recent"]]
    lo, hi = (as_of - timedelta(days=365)).date().isoformat(), as_of.date().isoformat()
    # older filings live in extra pages; only fetch those covering the year before the decision time (busy
    # issuers such as banks file thousands of notes a year, and every page is several MB)
    pages = [f for f in sub["filings"].get("files", [])
             if f.get("filingFrom", "9999") <= hi and f.get("filingTo", "0000") >= lo]
    for f in pages[:3]:
        blocks.append(_json((await gw.fetcher.fetch(f"https://data.sec.gov/submissions/{f['name']}",
                                                    headers=hdr, max_bytes=8_000_000)).text))
    forms = set(a["forms"])
    rows = []
    for b in blocks:
        for i, form in enumerate(b["form"]):
            if form not in forms:
                continue
            accepted = sec_accepted_utc(b["acceptanceDateTime"][i])
            if accepted > as_of:
                continue
            acc = b["accessionNumber"][i].replace("-", "")
            rows.append({"form": form, "filed": b["filingDate"][i], "accepted_utc_latest": accepted.isoformat(),
                         "items": (b.get("items") or [""] * (i + 1))[i],
                         "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{b['primaryDocument'][i]}"})
    rows.sort(key=lambda x: x["accepted_utc_latest"], reverse=True)
    return {"ticker": t, "cik": cik, "filings": rows[: a["limit"]],
            "note": "8-K earnings news is usually in exhibit EX-99.1: list it with filing_documents"}


async def _filing_accepted(gw: ToolGateway, cik: str, acc: str) -> datetime:
    dashed = f"{acc[:10]}-{acc[10:12]}-{acc[12:]}"
    r = await gw.fetcher.fetch(f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{dashed}-index-headers.html",
                               headers={"User-Agent": gw.sec_user_agent})
    m = re.search(r"ACCEPTANCE-DATETIME>\s*(\d{14})", r.text)
    if r.status != 200 or not m:
        raise FetchError("could not confirm when this filing was accepted")
    return sec_accepted_utc(m.group(1))


def _sec_parts(url: str) -> tuple[str, str]:
    m = SEC_ARCHIVE.match(url)
    if not m:
        raise FetchError("only https://www.sec.gov/Archives/edgar/data/... filing URLs are allowed")
    return m.group(1), m.group(2)


async def filing_documents(gw: ToolGateway, a: dict[str, Any]) -> Any:
    as_of = _as_of(gw)
    cik, acc = _sec_parts(a["url"])
    if await _filing_accepted(gw, cik, acc) > as_of:
        raise FetchError("filing was accepted after the decision time")
    r = await gw.fetcher.fetch(f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/index.json",
                               headers={"User-Agent": gw.sec_user_agent})
    items = _json(r.text).get("directory", {}).get("item", [])
    docs = [{"name": i["name"], "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{i['name']}"}
            for i in items if re.search(r"\.(htm|html|txt)$", i["name"], re.IGNORECASE) and "index" not in i["name"]]
    return {"documents": docs[:20]}


async def read_filing(gw: ToolGateway, a: dict[str, Any]) -> Any:
    as_of = _as_of(gw)
    cik, acc = _sec_parts(a["url"])
    accepted = await _filing_accepted(gw, cik, acc)
    if accepted > as_of:
        raise FetchError("filing was accepted after the decision time")
    r = await gw.fetcher.fetch(a["url"], headers={"User-Agent": gw.sec_user_agent}, max_bytes=8_000_000)
    if r.status != 200:
        raise FetchError(f"HTTP {r.status}")
    page: dict[str, str] = html_to_text(r.text, a["max_chars"]) if "html" in r.content_type.lower() or \
        a["url"].endswith(("htm", "html")) else {"title": "", "text": r.text[: a["max_chars"]]}
    return {"url": a["url"], "accepted_utc_latest": accepted.isoformat(), "title": page.get("title", ""),
            "text": page["text"]}


# ---------------- Internet Archive ----------------

_INDEX_LOCKS: dict[str, asyncio.Lock] = {}


async def capture_index(gw: ToolGateway, url: str) -> list[tuple[str, str]]:
    """Every day's capture of `url` (timestamp, original), fetched ONCE per URL and kept on disk.

    The list is fetched today, so it also names captures made after a decision time; callers only ever pick
    from those at or before it (and the page itself is then read from that capture). Knowing that a later copy
    exists tells the model nothing: it never sees the list. One lookup per page instead of one per page per
    month is what keeps a backtest inside the Internet Archive's ~15 requests/minute."""
    key = hashlib.sha256(url.encode()).hexdigest()
    path = gw.tool_cache / "cdx" / f"{key}.json" if gw.tool_cache is not None else None
    lock = _INDEX_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:  # several decisions may want the same page at once: ask the archive only once
        if path is not None and path.exists():
            return [(t, o) for t, o in json.loads(path.read_text())["captures"]]
        r = await gw.fetcher.fetch(f"{CDX}?url={quote(url, safe='')}&filter=statuscode:200&collapse=timestamp:8"
                                   "&fl=timestamp,original&output=json", max_bytes=16_000_000)
        if r.status != 200:
            raise FetchError(f"archive index HTTP {r.status}")
        rows = _json(r.text) if r.text.strip() else []
        caps = [(str(x[0]), str(x[1])) for x in rows[1:]] if rows else []
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"url": url, "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
                                       "captures": caps}))
            tmp.replace(path)
        return caps


async def _latest_capture(gw: ToolGateway, url: str, as_of: datetime, window_days: int) -> tuple[str, str] | None:
    lo, hi = _stamp(as_of - timedelta(days=window_days)), _stamp(as_of)
    before = [c for c in await capture_index(gw, url) if lo <= c[0] <= hi]
    return max(before) if before else None


async def _read_capture(gw: ToolGateway, ts: str, original: str, as_of: datetime, max_chars: int) -> dict[str, Any]:
    r = await gw.fetcher.fetch(f"https://web.archive.org/web/{ts}id_/{original}", max_bytes=4_000_000)
    m = ARCHIVE_TS.match(r.final_url)
    got = m.group(1) if m else ts
    if got > _stamp(as_of):  # the archive redirects to the nearest capture, which may be a later one
        raise FetchError("the archive only has a copy from after the decision time")
    if r.status != 200:
        raise FetchError(f"archive HTTP {r.status}")
    page = html_to_text(r.text, max_chars)
    return {"url": original, "captured_utc": datetime.strptime(got, "%Y%m%d%H%M%S").replace(tzinfo=UTC).isoformat(),
            "title": page.get("title", ""), "text": page["text"]}


async def archived_page(gw: ToolGateway, a: dict[str, Any]) -> Any:
    as_of = _as_of(gw)
    url = a["url"]
    host = (urlsplit(url if "://" in url else "http://" + url).hostname or "").lower()
    if not host or host.endswith("archive.org"):
        raise FetchError("give the original page URL, not an archive link")
    cap = await _latest_capture(gw, re.sub(r"^https?://", "", url), as_of, a["window_days"])
    if cap is None:
        raise FetchError(f"no archived copy in the {a['window_days']} days before the decision time")
    return await _read_capture(gw, *cap, as_of, a["max_chars"])


async def news_as_of(gw: ToolGateway, a: dict[str, Any]) -> Any:
    """One source at a time: the Internet Archive allows about 15 requests a minute (it blocks faster clients
    for an hour), and the host pacing in app/tools/netguard.py enforces that."""
    as_of = _as_of(gw)
    t = a["ticker"].strip().upper()
    tried = []
    for source, tpl, y0, y1 in NEWS_PAGES:
        if not y0 <= as_of.year <= y1:
            continue
        tried.append(source)
        try:
            cap = await _latest_capture(gw, tpl.format(T=t, t=t.lower()), as_of, a["window_days"])
            if cap is not None:
                return {"source": source, **await _read_capture(gw, *cap, as_of, a["max_chars"])}
        except FetchError:
            continue
    raise FetchError(f"no archived news page for {t} in the {a['window_days']} days before the decision time "
                     f"(tried {', '.join(dict.fromkeys(tried))})")


# ---------------- local prices ----------------

async def price_history_as_of(gw: ToolGateway, a: dict[str, Any]) -> Any:
    as_of = _as_of(gw)
    if gw.price_lookup is None:
        raise FetchError("no local price file configured")
    t = a["ticker"].strip().upper()
    rows = gw.price_lookup(t, as_of, a["days"] + 1)
    if len(rows) < 2:
        raise FetchError(f"no prices for {t} before the decision time")
    closes = [c for _, c in rows]

    def ret(n: int) -> float | None:
        return round(closes[-1] / closes[-1 - n] - 1, 4) if len(closes) > n else None
    daily = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))][-20:]
    vol = (sum(x * x for x in daily) / len(daily)) ** 0.5 * 252 ** 0.5
    return {"ticker": t, "last_date": rows[-1][0], "last_close": round(closes[-1], 2), "ret_1d": ret(1),
            "ret_5d": ret(5), "ret_20d": ret(20), "ret_60d": ret(60), "vol_20d_annualized": round(vol, 3),
            "closes_last_30": [round(c, 2) for c in closes[-30:]]}
