"""Arm B3, step 1 (docs/PLAN_60_V2.md "Arm B3", fixed before arm B2's verdict): rebuild Bonsai's research evidence by
code from the same as-of material, so the news headlines and the previous quarter's outlook actually reach it.

    python scripts/b3_evidence.py          # both samples; fetches missing previous releases from SEC once; resumable

Writes results/events_research_Jan-v1-4B-GGUF_Q4_K_M_v3b3{_2024}/<accession>.json: B2's research record with its
"evidence" replaced (6,000 characters at most):
  1. the previous quarter's outlook paragraphs (<= 2,000), 2. the news headlines (<= 1,200), 3. Jan's own rounds after
  the first look, then Jan's final reason (the rest).
"""
from __future__ import annotations

import asyncio
import gzip
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import pandas as pd
from llm_fields import research_folder
from research_events import WEBCACHE, previous_releases

from app.tools.gateway import TOOLS, ToolGateway, validate_args

TEXT = BACKEND / "data" / "events" / "text"
ALL_EVENTS = BACKEND / "data" / "events" / "events_2024-01-01_2026-09-24.csv"
SAMPLES = (("2024", "data/events/events_sp500_2024.csv", "features_sp500_2024_secchk.csv"),
           ("2025", "data/events/events_sp500_2025.csv", "features_sp500_2025_secchk.csv"))
BUDGET, OUTLOOK_MAX, NEWS_MAX = 6000, 2000, 1200
OUTLOOK_RE = re.compile(r"outlook|guidance|expects?|anticipates?|forecast|full[- ]year|fiscal (year )?20\d\d", re.IGNORECASE)
HEADING_RE = re.compile(r"outlook|guidance", re.IGNORECASE)
BOILERPLATE = ("Yahoo", "Subscribe", "Real Time Price", "Currency in", "Trade prices", "Fair Value",
               "actionable insight", "All rights reserved", "As of ")


def outlook(text: str, max_chars: int = OUTLOOK_MAX) -> str:
    """Paragraphs that talk about the outlook and carry a number; those after an Outlook/Guidance heading first."""
    paras = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    head = next((i for i, p in enumerate(paras) if len(p) < 60 and HEADING_RE.search(p)), None)
    hits = [i for i, p in enumerate(paras) if len(p) >= 40 and OUTLOOK_RE.search(p) and re.search(r"\d", p)]
    order = ([i for i in hits if i > head] + [i for i in hits if i <= head]) if head is not None else hits
    out, n = [], 0
    for i in order:
        p = paras[i]
        if n + len(p) + 1 > max_chars:
            if not out:
                out.append(p[:max_chars])
            break
        out.append(p)
        n += len(p) + 1
    return "\n".join(out)


def headlines(page: dict[str, Any], as_of: datetime, max_chars: int = NEWS_MAX) -> str:
    lines = [x.strip() for x in str(page.get("text", "")).splitlines()]
    keep = [x for x in lines if 25 <= len(x) <= 200 and not any(b.lower() in x.lower() for b in BOILERPLATE)]
    if not keep:
        return ""
    cap = str(page.get("captured_utc", ""))[:10]
    try:
        age = f", {(as_of.date() - datetime.fromisoformat(cap).date()).days} days before the release"
    except ValueError:
        age = ""
    out = f"(news page snapshot from {cap or 'an unknown date'}{age})"
    for x in keep:
        if len(out) + len(x) + 3 > max_chars:
            break
        out += "\n- " + x
    return out


def news_page(ticker: str, as_of: datetime) -> dict[str, Any] | None:
    gw = ToolGateway(mode="as_of", fetcher=None, as_of=as_of, tool_cache=WEBCACHE)  # type: ignore[arg-type]
    c = gw._cache_path("news_as_of", validate_args(TOOLS["news_as_of"], {"ticker": ticker}))
    if c is None or not c.exists():
        return None
    rec = json.loads(c.read_text())
    try:
        page = json.loads(rec["result"]) if rec.get("ok") else None
    except json.JSONDecodeError:  # a result cut at 5,000 characters: not valid JSON any more
        m = re.search(r'"captured_utc": "([^"]+)".*?"text": "(.*)', rec["result"], re.DOTALL)
        page = {"captured_utc": m.group(1), "text": m.group(2).encode().decode("unicode_escape", "ignore")} if m else None
    return page if isinstance(page, dict) else None


def jan_rest(rec: dict[str, Any]) -> str:
    ev = str(rec.get("evidence") or "")
    i = ev.find("[round 1]")
    out = ev[i:] if i >= 0 else ""
    if rec.get("answered") and rec.get("reason"):
        out += "\n\nJan's conclusion: " + str(rec["reason"])
    return out


def build(prev_text: str | None, prev_date: str, page: dict[str, Any] | None, as_of: datetime,
          rec: dict[str, Any]) -> tuple[str, dict[str, bool]]:
    o = outlook(prev_text) if prev_text else ""
    h = headlines(page, as_of) if page else ""
    parts = ["== Previous quarter's outlook (the company's previous earnings release"
             + (f", {prev_date}" if prev_date else "") + ") ==\n"
             + (o or f"No outlook found in the previous release{f' ({prev_date})' if prev_date else ''}."),
             "== News headlines ==\n" + (h or "No news headlines found.")]
    used = sum(len(x) + 2 for x in parts)
    j = jan_rest(rec)
    if j:
        parts.append("== Jan's research ==\n" + j[: max(0, BUDGET - used - 30)])
    return "\n\n".join(parts)[:BUDGET], {"outlook": bool(o), "news": bool(h), "jan": bool(j)}


async def fetch_missing(urls: dict[str, str]) -> None:
    from build_events import Sec
    from extract_events import html_to_text

    from app.tools.gateway import _read_env_file
    todo = {a: u for a, u in urls.items() if not (TEXT / f"{a}.txt.gz").exists()}
    print(f"previous releases to fetch from SEC: {len(todo)}", flush=True)
    if not todo:
        return
    sec = Sec(_read_env_file(BACKEND / ".env")["SEC_USER_AGENT"])
    sem = asyncio.Semaphore(4)

    async def one(a: str, u: str) -> None:
        async with sem:
            resp = await sec.get(u)
        if resp is not None:
            page = html_to_text(resp.text, 200_000)["text"] if "htm" in u.lower() else resp.text
            (TEXT / f"{a}.txt.gz").write_bytes(gzip.compress(page.encode()))
    items = list(todo.items())
    for i in range(0, len(items), 100):
        await asyncio.gather(*(one(a, u) for a, u in items[i:i + 100]))
    await sec.client.aclose()


def main() -> None:
    allev = pd.read_csv(ALL_EVENTS)
    prev = previous_releases(allev)
    url2 = {u: (a, str(t)[:10]) for a, u, t in zip(allev["accession"], allev["ex99_url"], allev["accepted_utc"],
                                                     strict=True) if isinstance(u, str)}
    asyncio.run(fetch_missing({url2[u][0]: u for u in prev.values() if u in url2}))
    stats: dict[str, dict[str, int]] = {}
    for tag, events, feats in SAMPLES:
        keep = set(pd.read_csv(BACKEND / "results" / "events" / feats)["accession"])
        src, dst = research_folder(tag, "b2"), research_folder(tag, "b3")
        dst.mkdir(parents=True, exist_ok=True)
        st = {"releases": 0, "outlook": 0, "news": 0, "jan": 0, "no_b2_record": 0}
        for r in pd.read_csv(BACKEND / events).itertuples():
            if r.accession not in keep:
                continue
            st["releases"] += 1
            sp = src / f"{r.accession}.json"
            if not sp.exists():
                st["no_b2_record"] += 1
                continue
            rec = json.loads(sp.read_text())
            as_of = datetime.fromisoformat(str(r.accepted_utc)).replace(tzinfo=UTC)
            pa, pdate = url2.get(prev.get(r.accession, ""), ("", ""))
            pt = gzip.decompress((TEXT / f"{pa}.txt.gz").read_bytes()).decode() if pa and (TEXT / f"{pa}.txt.gz").exists() else None
            ev, found = build(pt, pdate, news_page(str(r.ticker), as_of), as_of, rec)
            for k, v in found.items():
                st[k] += int(v)
            tmp = dst / f"{r.accession}.tmp"
            tmp.write_text(json.dumps(rec | {"evidence": ev, "b3_found": found}))
            tmp.replace(dst / f"{r.accession}.json")
        stats[tag] = st
        print(tag, st, flush=True)
    (BACKEND / "results" / "events" / "b3_evidence_stats.json").write_text(json.dumps(stats, indent=1) + "\n")


if __name__ == "__main__":
    main()
