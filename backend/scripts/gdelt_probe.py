#!/usr/bin/env python3
"""Feasibility-only, resumable GDELT coverage probe for first 150 B4 releases."""
import csv
import datetime as dt
import json
import re
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "backend/data/events/events_b4_2026.csv"
MEMBERS = ROOT / "backend/data/xbrl/members_2010_2026.csv"
OUT = ROOT / "backend/data/events/gdelt_probe_b4.jsonl"
URL = "https://api.gdeltproject.org/api/v2/doc/doc"
UA = "airp-research (paper trading research)"
UTC = dt.UTC


def clean_name(value):
    value = re.sub(r"\([^)]*\)", " ", value)
    value = re.sub(r"/[^/]*/", " ", value)
    value = re.sub(r"[^A-Za-z0-9&' -]", " ", value).replace("&", " ")
    words = value.split()
    suffixes = {"inc", "corp", "corporation", "co", "company", "ltd", "plc", "holdings", "group"}
    while words and words[-1].rstrip(".,").lower() in suffixes:
        words.pop()
    if words and words[0].lower() == "the":
        words.pop(0)
    return " ".join(words[:4])


def load_names():
    by_cik = {}
    with MEMBERS.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            by_cik[row["cik"].lstrip("0")] = row["name"]
    return by_cik


def query(q, start, end):
    params = {"query": q, "mode": "artlist", "format": "json", "maxrecords": 75,
              "sort": "datedesc", "startdatetime": start, "enddatetime": end}
    req = urllib.request.Request(URL + "?" + urllib.parse.urlencode(params), headers={"User-Agent": UA})
    attempts = 0
    retries = []
    while True:
        last = getattr(query, "last_start", None)
        if last is not None:
            time.sleep(max(0, 20 - (time.monotonic() - last)))  # GDELT throttled 6 s pacing (12/30 errors)
        query.last_start = time.monotonic()
        attempts += 1
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                body = response.read()
                elapsed = time.monotonic() - started
                return json.loads(body), response.status, elapsed, retries, None
        except urllib.error.HTTPError as e:
            status = e.code
            err = f"HTTP {status}"
        except Exception as e:  # noqa: BLE001 - any network failure is retried and logged
            status = None
            err = f"{type(e).__name__}: {e}"
        retries.append(err)
        if status == 429 and attempts <= 4:
            time.sleep(180)
            continue
        if status is not None and 500 <= status <= 599 and attempts <= 2:
            time.sleep(30)
            continue
        if status is None and attempts <= 2:
            time.sleep(30)
            continue
        return {}, status, time.monotonic() - started, retries, err


def main():
    by_cik = load_names()
    with SAMPLE.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))[:150]
    done = {}
    if OUT.exists():
        with OUT.open(encoding="utf-8") as f:
            for line in f:
                try:
                    x = json.loads(line)
                    done[x["accession"]] = x
                except Exception:  # noqa: BLE001, S110 - skip a torn log line
                    pass
    # Preserve successful records; retry all records that lack a successful response.
    done = {k: v for k, v in done.items() if v.get("status") == "success" or
            (not v.get("errors") and v.get("http_status") == 200)}
    completed = dict(done)
    timings, retry_count, hard_errors = [], 0, 0
    with OUT.open("a", encoding="utf-8") as out:
        for i, row in enumerate(rows, 1):
            acc, ticker = row["accession"], row["ticker"].upper()
            if acc in done:
                out.write(json.dumps(done[acc], ensure_ascii=False) + "\n")
                continue
            name = clean_name(by_cik.get(row["cik"].lstrip("0"), ticker))
            accepted = dt.datetime.fromisoformat(row["accepted_utc"]).replace(tzinfo=UTC)  # naive = UTC, never local
            start = (accepted - dt.timedelta(days=45)).strftime("%Y%m%d%H%M%S")
            end = (accepted - dt.timedelta(minutes=1)).strftime("%Y%m%d%H%M%S")
            data, status, elapsed, retries, err = query(f'"{name}" sourcelang:english', start, end)
            timings.append(elapsed)
            retry_count += len(retries)
            articles = {}
            if isinstance(data, dict):
                for article in data.get("articles", []):
                    try:
                        seen = dt.datetime.strptime(article["seendate"], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
                        if seen < accepted and article.get("url"):
                            articles[article["url"]] = {**article, "_seen": seen}
                    except Exception:  # noqa: BLE001, S112 - skip an article without a usable date
                        continue
            sorted_articles = sorted(articles.values(), key=lambda a: a["_seen"], reverse=True)
            record = {"accession": acc, "ticker": ticker, "clean_name": name,
                      "n_name_articles": len(articles), "n_asof_ok": len(articles),
                      "first_3_titles": [a.get("title", "") for a in sorted_articles[:3]],
                      "http_status": status, "seconds_taken": round(elapsed, 3),
                      "retries": retries, "errors": [err] if err else [],
                      "status": "success" if err is None else "hard_error",
                      "accepted_utc": row["accepted_utc"], "month": accepted.strftime("%Y-%m"),
                      "name_urls": list(articles)}
            if err:
                hard_errors += 1
            completed[acc] = record
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()
            if i % 10 == 0:
                print(f"progress {i}/150; hard_errors={hard_errors}; retries={retry_count}", flush=True)
            if i >= 30 and hard_errors / i > 0.20:
                print(f"STOP: hard errors {hard_errors}/{i} exceed 20%", flush=True)
                break
    print(json.dumps({"completed": len(completed), "hard_errors": hard_errors,
                      "retries": retry_count,
                      "median_seconds_per_request": statistics.median(timings) if timings else None}, indent=2))


if __name__ == "__main__":
    main()
