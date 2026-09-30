#!/usr/bin/env python3
"""Warm Jan's as-of news cache with frozen GDELT DOC 2.0 headlines."""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from gdelt_probe import clean_name, load_names
from research_events import WEBCACHE

from app.tools.gateway import TOOLS, ToolGateway, validate_args

SAMPLE = BACKEND / "data/events/events_b4_2026.csv"
LOG = BACKEND / "results/events/warm_gdelt.jsonl"
URL = "https://api.gdeltproject.org/api/v2/doc/doc"
UA = "airp-research (paper trading research)"
MIN_START_GAP = 20.0
_last_request_start: float | None = None


def make_query(name: str) -> str:
    q = f'"{name}"'
    if len(name.split()) == 1:
        q += " (stock OR shares OR earnings OR NYSE OR Nasdaq)"
    return q + " sourcelang:english"


def parse_accepted(value: str) -> datetime:
    accepted = datetime.fromisoformat(value)
    return accepted.replace(tzinfo=UTC) if accepted.tzinfo is None else accepted.astimezone(UTC)


def asof_headlines(articles: list[dict], accepted: datetime) -> list[dict[str, str]]:
    headlines = []
    for article in articles:
        try:
            seen = datetime.strptime(article["seendate"], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        except (KeyError, TypeError, ValueError):
            continue
        if seen >= accepted:
            continue
        headlines.append({"date": seen.strftime("%Y-%m-%d %H:%M"),
                          "domain": str(article.get("domain", "")),
                          "title": str(article.get("title", "")), "_sort": seen.isoformat()})
    headlines.sort(key=lambda h: h["_sort"], reverse=True)
    return [{k: h[k] for k in ("date", "domain", "title")} for h in headlines[:15]]


def fetch_gdelt(query: str, accepted: datetime) -> list[dict]:
    global _last_request_start
    start = (accepted - timedelta(days=45)).strftime("%Y%m%d%H%M%S")
    end = (accepted - timedelta(minutes=1)).strftime("%Y%m%d%H%M%S")
    params = {"query": query, "mode": "artlist", "format": "json", "maxrecords": 75,
              "sort": "datedesc", "startdatetime": start, "enddatetime": end}
    req = urllib.request.Request(URL + "?" + urllib.parse.urlencode(params), headers={"User-Agent": UA})
    attempt = 0
    while True:
        if _last_request_start is not None:
            time.sleep(max(0, MIN_START_GAP - (time.monotonic() - _last_request_start)))
        _last_request_start = time.monotonic()
        attempt += 1
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                body = json.loads(response.read())
            return body.get("articles", []) if isinstance(body, dict) else []
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt <= 4:
                time.sleep(180)
                continue
            if 500 <= exc.code <= 599 and attempt <= 3:
                time.sleep(30)
                continue
            raise
        except (TimeoutError, urllib.error.URLError, OSError):
            if attempt <= 3:
                time.sleep(30)
                continue
            raise


def cache_path(ticker: str, accepted: datetime) -> Path:
    gw = ToolGateway.from_env("as_of", as_of=accepted, tool_cache=WEBCACHE, max_result_chars=5000)
    try:
        args = validate_args(TOOLS["news_as_of"], {"ticker": ticker})
        path = gw._cache_path("news_as_of", args)
        assert path is not None
        return path
    finally:
        # from_env constructs a SafeFetcher; cache computation itself does no I/O.
        import asyncio
        asyncio.run(gw.aclose())


def store_cache(path: Path, result: dict) -> None:
    text = json.dumps(result, ensure_ascii=False)
    text = text[:5000] + "…(truncated)" if len(text) > 5000 else text
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"ok": True, "result": text}, ensure_ascii=False))
    tmp.replace(path)


def load_logged() -> dict[str, dict]:
    done = {}
    if LOG.exists():
        for line in LOG.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
                done[record["accession"]] = record
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def run(limit: int | None = None) -> list[dict]:
    with SAMPLE.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if limit is not None:
        rows = rows[:limit]
    done = load_logged()
    company_names = load_names()
    processed = [r for r in rows if done.get(r["accession"], {}).get("status") in {"ok", "none"}]
    new_records = []
    begun = time.monotonic()
    for index, row in enumerate(rows, 1):
        accession = row["accession"]
        if done.get(accession, {}).get("status") in {"ok", "none"}:
            continue
        started = time.monotonic()
        accepted = parse_accepted(row["accepted_utc"])
        name = clean_name(row["company_name"] if "company_name" in row else row.get("name", ""))
        if not name:
            # Reuse the same CIK-to-company-name source as the feasibility probe.
            name = clean_name(company_names.get(row["cik"].lstrip("0"), row["ticker"]))
        query = make_query(name)
        rec = {"accession": accession, "ticker": row["ticker"], "query": query}
        try:
            articles = fetch_gdelt(query, accepted)
            headlines = asof_headlines(articles, accepted)
            result = {"source": "GDELT DOC 2.0", "as_of": accepted.isoformat(), "window_days": 45,
                      "query": query, "headlines": headlines}
            store_cache(cache_path(str(row["ticker"]), accepted), result)
            rec["n_asof"] = len(headlines)
            rec["status"] = "ok" if headlines else "none"
        except Exception as exc:  # noqa: BLE001 - log any per-release fetch/cache failure and continue
            rec.update({"n_asof": 0, "status": "error", "error": f"{type(exc).__name__}: {exc}"[:200]})
        rec["seconds"] = round(time.monotonic() - started, 3)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        new_records.append(rec)
        processed.append(rec)
        if index % 25 == 0:
            elapsed = time.monotonic() - begun
            good = sum(r.get("n_asof", 0) > 0 for r in processed)
            rate = elapsed / max(1, len(new_records))
            remaining = sum(done.get(r["accession"], {}).get("status") not in {"ok", "none"}
                            for r in rows[index:])
            print(f"progress {index}/{len(rows)}; coverage {good}/{len(processed)} "
                  f"({100 * good / max(1, len(processed)):.1f}%); ETA {remaining * rate / 3600:.1f}h", flush=True)
    return new_records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    run(args.limit)


if __name__ == "__main__":
    main()
