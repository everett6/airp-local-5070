"""Bounded original-page retrieval for current research, independent of model tool choices."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.tools.gateway import ToolGateway

VERSION = "original_articles_v3"


def publisher(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    if not host or host == "sec.gov" or host.endswith(".sec.gov") or host in {"news.google.com", "bing.com"}:
        return ""
    # Treat subdomains of the same publisher as one source (including common UK suffixes).
    labels = host.split('.')
    return '.'.join(labels[-3:] if host.endswith(('.co.uk', '.com.au', '.co.jp')) else labels[-2:])


def canonical_url(url: str) -> str:
    """Drop feed tracking only; preserve meaningful query parameters."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {".tsrc", "tsrc", "gclid", "fbclid"}]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def payload(response: dict[str, Any]) -> dict[str, Any]:
    if not response.get("ok"):
        return {}
    try:
        data = json.loads(response.get("result", "{}"))
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def domain_rank(stats: dict[str, dict[str, int]], domain: str) -> float:
    """Share of earlier attempts on this site that gave a usable page (unknown sites rank in the middle)."""
    s = stats.get(domain, {})
    tries = s.get("ok", 0) + s.get("fail", 0)
    return 0.5 if tries < 3 else s.get("ok", 0) / tries


async def collect(gw: ToolGateway, ticker: str, name: str, now: datetime, wide: bool = False,
                  stats: dict[str, dict[str, int]] | None = None) -> dict[str, Any]:
    """Try at most 20 pages in parallel groups of four; robots/HTTP refusals remain refusals.

    News discovery is deterministic, so an early model final cannot skip the source gate.
    Preserve fetched text and failures; headlines and HTTP-200 consent pages do not qualify.
    wide: try up to 16 pages in the first pass and keep up to 12,000 characters of each, instead of stopping once
    two publishers are found (the parallel readers in app/sandbox/page_readers.py read all of them).
    """
    name = re.sub(r"\s+(?:Inc\.?|Corporation|Corp\.?|Ltd\.?)\s*$", "", name, flags=re.IGNORECASE).strip(" ,") or ticker
    queries = [f'"{name}"', f'"{name}" earnings', f'"{name}" products']
    if wide:
        queries += [f'"{name}" guidance outlook', f'"{name}" analyst price target', f'"{name}" lawsuit OR regulator OR probe',
                    f'"{name}" customers contract deal', f'"{name}" competition']
    window = 30 if wide else 14
    reqs = [{"id": "articles.news", "tool": "stock_news", "args": {"ticker": ticker, "limit": 12}}]
    reqs += [{"id": f"articles.search.{i}", "tool": "news_search",
              "args": {"query": q, "days": window, "limit": 12}} for i, q in enumerate(queries)]
    pages: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for phase in range(2):
        limit = (28 if wide else 12) if phase == 0 else 8
        if phase:
            if len({p["publisher"] for p in pages}) >= 2:
                break
            exclusions = " ".join(f"-site:{d}" for d in sorted({publisher(u) for u in seen}) if d)
            reqs = [{"id": f"articles.fallback.{i}", "tool": "news_search",
                     "args": {"query": q + " " + exclusions, "days": 14, "limit": 12}}
                    for i, q in enumerate((f'"{name}" press release', f'{ticker} stock company news'))]
        responses = await gw.execute(reqs)
        candidates: dict[str, list[dict[str, Any]]] = {}
        tokens = [ticker.lower(), name.lower(), name.split()[0].lower()]
        for response in responses.values():
            for item in payload(response).get("items", []):
                if not isinstance(item, dict):
                    continue
                url = canonical_url(str(item.get("url", "")))
                item = {**item, "url": url}
                domain = publisher(url)
                title = str(item.get("title", "")).lower()
                if url in seen or not domain or item.get("fetchable") is False or not any(t in title for t in tokens):
                    continue
                try:
                    date = datetime.fromisoformat(str(item.get("published", "")))
                    if date.tzinfo is None or not now - timedelta(days=window) <= date <= now:
                        continue
                except ValueError:
                    continue
                if not any(x["url"] == url for x in candidates.setdefault(domain, [])):
                    candidates[domain].append(item)
        # Round-robin publishers: do not spend the entire budget on Yahoo syndication. Sites that usually give a
        # usable page come first; a page the site's robots.txt disallows is skipped before it costs an attempt.
        by_rank = sorted(candidates.items(), key=lambda kv: -domain_rank(stats or {}, kv[0]))
        ordered: list[dict[str, Any]] = []
        while any(items for _, items in by_rank) and len(ordered) < limit:
            for _, items in by_rank:
                while items and len(ordered) < limit:
                    item = items.pop(0)
                    if wide and not await gw.robots_allowed(item["url"]):
                        attempts.append({"url": item["url"], "ok": False, "usable": False, "skipped": True,
                                         "error": "skipped: the site's robots.txt disallows it (not fetched)"})
                        continue
                    ordered.append(item)
                    break
        for start in range(0, len(ordered), 4):
            batch = ordered[start:start + 4]
            fetches: list[dict[str, Any]] = [{"id": f"articles.page.{phase}.{start + i}", "tool": "fetch_page",
                        "args": {"url": x["url"], "max_chars": 12000 if wide else 3800}} for i, x in enumerate(batch)]
            seen.update(x["url"] for x in batch)
            fetched = await gw.execute(fetches)
            for request, item in zip(fetches, batch, strict=True):
                response = fetched.get(request["id"], {})
                page = payload(response)
                text = str(page.get("text", ""))
                domain = publisher(str(page.get("url", "")))
                usable = bool(domain and len(text) >= 400 and any(t in text.lower() for t in tokens))
                if page.get('published'):
                    try:
                        date = datetime.fromisoformat(str(page['published']))
                        usable = usable and date.tzinfo is not None and now - timedelta(days=window) <= date <= now
                    except ValueError:
                        usable = False
                attempts.append({"url": item["url"], "ok": bool(response.get("ok")), "usable": usable,
                                 "error": response.get("error") or (None if usable else "no relevant article body")})
                if usable:
                    pages.append({**page, "publisher": domain, "discovered_published": item["published"],
                                  "retrieved_at": datetime.now(UTC).isoformat(),
                                  "text_sha256": hashlib.sha256(text.encode()).hexdigest()})
            if len({p["publisher"] for p in pages}) >= 2 and not wide:
                break
    # One observation block per page keeps number validation confined to its source.
    evidence = "[original article retrieval]\n" + "\n".join(
        "  - fetch_page(" + json.dumps({"url": p["url"]}) + ") -> " + json.dumps(p) for p in pages)
    return {"version": VERSION, "pages": pages, "attempts": attempts,
            "domains": sorted({p["publisher"] for p in pages}), "evidence": evidence if pages else ""}


async def filings(gw: ToolGateway, ticker: str, limit: int = 3) -> list[dict[str, Any]]:
    """The company's own latest filings as pages for the readers: each 8-K's press-release exhibit (EX-99.*) and the
    main document of the latest 10-Q/10-K, up to 16,000 characters each. SEC text is read, never counted as a
    news publisher."""
    got = await gw.execute([{"id": "f.list", "tool": "sec_filings",
                             "args": {"ticker": ticker, "forms": ["8-K", "10-Q", "10-K"], "limit": 6}}])
    rows = [f for f in payload(got.get("f.list", {})).get("filings", []) if isinstance(f, dict) and f.get("url")]
    picked = [f for f in rows if f.get("form") == "8-K"][:limit - 1] + [f for f in rows if f.get("form") in ("10-Q", "10-K")][:1]
    docs = await gw.execute([{"id": f"f.docs.{i}", "tool": "filing_documents", "args": {"url": f["url"]}}
                             for i, f in enumerate(picked)])
    reads = []
    for i, f in enumerate(picked):
        names = payload(docs.get(f"f.docs.{i}", {})).get("documents", [])
        ex = [d for d in names if re.search(r"ex-?99", d.get("name", ""), re.IGNORECASE)]
        main = [d for d in names if not re.search(r"^r\d|ex-?\d", d.get("name", ""), re.IGNORECASE)]
        for d in (ex[:1] if f.get("form") == "8-K" else main[:1]):
            reads.append((f, d["url"]))
    texts = await gw.execute([{"id": f"f.read.{i}", "tool": "read_filing", "args": {"url": u, "max_chars": 16000}}
                              for i, (_, u) in enumerate(reads)])
    pages = []
    for i, (f, u) in enumerate(reads):
        page = payload(texts.get(f"f.read.{i}", {}))
        if len(str(page.get("text", ""))) >= 400:
            pages.append({"url": u, "title": f"{f.get('form')} {page.get('title', '')}".strip(),
                          "published": str(f.get("accepted", "")), "text": page["text"], "publisher": "sec.gov",
                          "text_sha256": hashlib.sha256(page["text"].encode()).hexdigest()})
    return pages


def update_stats(stats: dict[str, dict[str, int]], attempts: list[dict[str, Any]]) -> None:
    """Count usable and failed page attempts per site (skipped pages are not attempts)."""
    for a in attempts:
        if a.get("skipped"):
            continue
        d = publisher(a["url"])
        if d:
            s = stats.setdefault(d, {"ok": 0, "fail": 0})
            s["ok" if a.get("usable") else "fail"] += 1
