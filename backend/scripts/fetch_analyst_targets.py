"""Analyst targets and rating actions as of each earnings release (the analyst_targets_as_of tool, code-parsed from
the last archived Yahoo quote page within 60 days before the SEC acceptance time). No LLM.

    python scripts/fetch_analyst_targets.py --features results/events/features_sp500_2025.csv
    python scripts/fetch_analyst_targets.py --features results/events/features_sp500_2024.csv --events data/events/events_sp500_2024.csv

The Internet Archive allows ~15 requests a minute (paced in app/tools/netguard.py), so this is ~4 s per release plus
one capture-index lookup per ticker; resumable, and every result is in the tool cache for exact replay. Writes
data/events/analyst_targets.jsonl: accession, ok, captured_utc, target_avg/low/high, raises, lowers, actions.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import pandas as pd

from app.tools.gateway import ToolGateway
from app.tools.netguard import SafeFetcher

OUT = BACKEND / "data" / "events" / "analyst_targets.jsonl"
WEBCACHE = BACKEND / "results" / "webcache"


async def run(args: argparse.Namespace) -> None:
    feats = pd.read_csv(BACKEND / args.features)
    ev = pd.read_csv(BACKEND / args.events).set_index("accession")
    rows = [json.loads(x) for x in OUT.read_text().splitlines()] if OUT.exists() else []
    done = {r["accession"] for r in rows if r["ok"] or str(r.get("error", "")).startswith("no archived")}  # retry 429/504/timeouts
    todo = [a for a in feats["accession"] if a not in done and a in ev.index]
    base = ToolGateway.from_env("as_of", as_of=datetime(2000, 1, 1, tzinfo=UTC))
    fetcher = SafeFetcher(base.fetcher.user_agent, timeout_s=90.0, total_timeout_s=140.0)  # quote pages are ~2 MB
    tick = await fetcher.fetch("https://www.sec.gov/files/company_tickers.json",
                               headers={"User-Agent": base.sec_user_agent}, max_bytes=5_000_000)
    names = {v["ticker"]: v["title"] for v in json.loads(tick.text).values()}  # headlines use the name, not the ticker
    await base.aclose()
    print(f"{len(todo)} releases to look up ({len(done)} done)", flush=True)
    q: asyncio.Queue[str] = asyncio.Queue()
    for a in todo:
        q.put_nowait(a)
    n, t0 = 0, time.monotonic()

    async def worker(f) -> None:
        nonlocal n
        while not q.empty():
            acc = q.get_nowait()
            r = ev.loc[acc]
            as_of = datetime.fromisoformat(str(r["accepted_utc"])).replace(tzinfo=UTC)
            gw = ToolGateway(mode="as_of", fetcher=fetcher, as_of=as_of, tool_cache=WEBCACHE)
            res = (await gw.execute([{"id": "t", "tool": "analyst_targets_as_of",
                                      "args": {"ticker": str(r["ticker"]).replace(".", "-"),
                                               "name": names.get(str(r["ticker"]).replace(".", "-"), "")[:100]}}]))["t"]
            rec = {"accession": acc, "ticker": r["ticker"], "ok": bool(res.get("ok"))}
            rec |= json.loads(res["result"]) if res.get("ok") else {"error": res.get("error")}  # results are JSON text
            f.write(json.dumps(rec) + "\n")
            f.flush()
            n += 1
            if n % 50 == 0:
                rate = (time.monotonic() - t0) / n
                print(f"  {n}/{len(todo)} {rate:.1f}s/release eta={(len(todo) - n) * rate / 3600:.1f}h", flush=True)

    try:
        with OUT.open("a") as f:
            await asyncio.gather(*(worker(f) for _ in range(args.workers)))
    finally:
        await fetcher.aclose()
    last = {r["accession"]: r for r in map(json.loads, OUT.read_text().splitlines())}  # a retry's row replaces the old one
    print(f"{sum(r['ok'] for r in last.values())} of {len(last)} releases have analyst targets", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--features", default="results/events/features_sp500_2025.csv")
    ap.add_argument("--events", default="data/events/events_sp500_2025.csv")
    ap.add_argument("--workers", type=int, default=4, help="use 1-2 when retrying archive 429s")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
