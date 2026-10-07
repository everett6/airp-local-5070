"""M1 research memory (docs/PLAN_60_V2.md, "research memory M1"): what Jan and Bonsai learned about a company, kept
on disk so the next research run reads only what is new.

Per company, results/research_memory/<TICKER>.json holds the checked facts (text, source, date, first seen), the
URLs already read and the past calls. `remember` is called after each company; `seen_urls` lets the readers skip
pages already read; `prior_brief` feeds the saved facts back into the merge; `past_block` gives Bonsai a dated
history; `area_note` gives Jan a few recent facts from the same theme. Nothing here changes a call by itself: it
only decides what is read again and what context the models see.
"""
from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2] / "results" / "research_memory"
MAX_FACTS = 60      # per company, newest kept
MAX_URLS = 400
MAX_CALLS = 20
FACT_TTL_DAYS = 120  # facts older than this (by their own date, else first seen) are dropped


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()[:160]


def _path(ticker: str, root: Path) -> Path:
    return root / f"{re.sub(r'[^A-Za-z0-9.-]', '_', ticker.upper())}.json"


def load(ticker: str, root: Path = ROOT) -> dict[str, Any]:
    try:
        return json.loads(_path(ticker, root).read_text())
    except (OSError, ValueError):
        return {"ticker": ticker.upper(), "facts": [], "urls": [], "calls": []}


def _save(mem: dict[str, Any], root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    p = _path(mem["ticker"], root)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(mem, indent=1))
    os.replace(tmp, p)


def _fresh(f: dict[str, Any], now: datetime) -> bool:
    stamp = str(f.get("date") or f.get("first_seen") or "")[:10]
    try:
        return datetime.fromisoformat(stamp).replace(tzinfo=UTC) >= now - timedelta(days=FACT_TTL_DAYS)
    except ValueError:
        return True


def remember(row: dict[str, Any], root: Path = ROOT, now: datetime | None = None) -> dict[str, Any]:
    """Fold one research row (live_research_test.py) into the company's memory and save it."""
    now = now or datetime.now(UTC)
    mem = load(row["ticker"], root)
    facts = {_key(str(f.get("text", ""))): f for f in mem["facts"]}
    for f in (row.get("wide_brief") or row.get("brief") or {}).get("facts", []) or []:
        k = _key(str(f.get("text", "")))
        if k and k not in facts:
            facts[k] = {"text": str(f["text"])[:400], "source": f.get("source", ""), "date": str(f.get("date") or "")[:10],
                        "first_seen": now.date().isoformat()}
    kept = [f for f in facts.values() if _fresh(f, now)]
    kept.sort(key=lambda f: str(f.get("date") or f.get("first_seen") or ""), reverse=True)
    mem["facts"] = kept[:MAX_FACTS]
    urls = list(dict.fromkeys([*mem["urls"], *(p.get("url") for p in (row.get("article_retrieval") or {}).get("pages", [])
                                                if p.get("url")), *(r.get("url") for r in row.get("reader_runs") or []
                                                                    if r.get("url") and not r.get("error"))]))
    mem["urls"] = urls[-MAX_URLS:]
    if row.get("status") == "decided":
        mem["calls"] = [*mem["calls"], {"decided_at": row.get("decided_at"), "ratings": row.get("ratings"),
                                        "primary": row.get("primary"), "bull_case": str(row.get("bull_case", ""))[:600],
                                        "bear_case": str(row.get("bear_case", ""))[:600]}][-MAX_CALLS:]
    mem["updated_at"] = now.isoformat()
    _save(mem, root)
    return mem


def seen_urls(ticker: str, root: Path = ROOT) -> set[str]:
    return set(load(ticker, root)["urls"])


def prior_brief(ticker: str, root: Path = ROOT, now: datetime | None = None) -> dict[str, Any]:
    """The saved facts as a reader brief, for page_readers.merge (merge drops duplicates and future dates)."""
    now = now or datetime.now(UTC)
    return {"facts": [f for f in load(ticker, root)["facts"] if _fresh(f, now)], "catalysts": [], "risks": []}


def past_block(ticker: str, root: Path = ROOT, calls: int = 3) -> str:
    """Bonsai's dated history of earlier calls: dates and sides only (the saved facts are already on the card)."""
    mem = load(ticker, root)
    if not mem["calls"]:
        return ""
    side = {1: "strong bear", 2: "bear", 4: "bull", 5: "strong bull"}
    lines = ["", "Past research on this company (saved by earlier runs; model judgements, not evidence):"]
    for c in mem["calls"][-calls:][::-1]:
        r = ", ".join(f"{h} {side.get(v, v)}" for h, v in (c.get("ratings") or {}).items())
        lines.append(f"- {str(c.get('decided_at', ''))[:10]}: {r}" + (f"; primary {c['primary']}" if c.get("primary") else ""))
    # Old bull/bear text stays out: the judge must not quote its own past opinion back as evidence.
    return "\n".join(lines)


def area_note(ticker: str, peers: list[str], root: Path = ROOT, n: int = 6) -> str:
    """A few of the newest saved facts about other companies in the same theme: Jan's view of the area."""
    pool = []
    for p in peers:
        if p.upper() != ticker.upper():
            pool += [(str(f.get("date") or f.get("first_seen") or ""), p, f["text"]) for f in load(p, root)["facts"][:3]]
    pool.sort(reverse=True)
    if not pool:
        return ""
    return "Context on the area (saved facts about peers, background only): " + " | ".join(
        f"{p} ({d}): {t[:160]}" for d, p, t in pool[:n])
