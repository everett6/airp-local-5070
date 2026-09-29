"""Arm B2, step 1 (docs/PLAN_60_V2.md "Arm B2"): fetch each release's as-of news page ONCE, slowly, into the tool cache.

    python scripts/warm_news.py            # 2024 then 2025-26 samples; resumable; network only, no GPU
    python scripts/warm_news.py b4         # arm B4's sample (scripts/b4_prep.py)

Why: in arm B, 16 research workers queued behind the Internet Archive's pacing (one request per 4 s) and 75% of news
calls hit their 15 s timeout, so Jan had news for 5% of 2024 releases (46% in 2025-26). This makes exactly the
call Jan's prefetch makes ({"tool": "news_as_of", "args": {"ticker": T}} at the release's acceptance time) one at a
time with a long timeout, and stores the result under the same cache key the gateway uses. Jan's rerun then reads
it from the cache. Nothing about the call, the as-of rule or the page parsing changes; only the waiting.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import pandas as pd
from research_events import WEBCACHE

from app.tools.gateway import TOOLS, ToolGateway, validate_args
from app.tools.netguard import FetchError, SafeFetcher

SAMPLES = (("2024", "data/events/events_sp500_2024.csv", "features_sp500_2024_secchk.csv"),
           ("2025-26", "data/events/events_sp500_2025.csv", "features_sp500_2025_secchk.csv"))
SAMPLES_B4 = (("b4", "data/events/events_b4_2026.csv", "features_b4_2026.csv"),)
LOG = BACKEND / "results" / "events" / "warm_news.jsonl"


async def main(samples: tuple[tuple[str, str, str], ...] = SAMPLES) -> None:
    base = ToolGateway.from_env("as_of", as_of=datetime(2000, 1, 1, tzinfo=UTC))
    fetcher = SafeFetcher(base.fetcher.user_agent, timeout_s=90.0, total_timeout_s=180.0)
    await base.aclose()
    spec = TOOLS["news_as_of"]
    done = {json.loads(x)["accession"] for x in LOG.read_text().splitlines()} if LOG.exists() else set()
    try:
        for tag, events, feats in samples:
            keep = set(pd.read_csv(BACKEND / "results" / "events" / feats)["accession"])
            rows = [r for r in pd.read_csv(BACKEND / events).itertuples()
                    if r.accession in keep and r.accession not in done]
            print(f"{tag}: {len(rows)} releases", flush=True)
            t0, ok = time.monotonic(), 0
            for i, r in enumerate(rows, 1):
                as_of = datetime.fromisoformat(str(r.accepted_utc)).replace(tzinfo=UTC)
                gw = ToolGateway(mode="as_of", fetcher=fetcher, as_of=as_of, tool_cache=WEBCACHE,
                                 max_result_chars=5000)
                args = validate_args(spec, {"ticker": str(r.ticker)})
                cache = gw._cache_path(spec.name, args)
                rec = {"accession": r.accession, "ticker": r.ticker, "sample": tag}
                if cache is not None and cache.exists():
                    rec["status"] = "cached"
                else:
                    try:
                        result = await asyncio.wait_for(spec.handler(gw, args), 600)
                        text = json.dumps(result, ensure_ascii=False, default=str)
                        text = text[:5000] + "…(truncated)" if len(text) > 5000 else text
                        assert cache is not None
                        cache.parent.mkdir(parents=True, exist_ok=True)
                        tmp = cache.with_suffix(".tmp")
                        tmp.write_text(json.dumps({"ok": True, "result": text}, ensure_ascii=False))
                        tmp.replace(cache)
                        rec["status"] = "fetched"
                    except (FetchError, TimeoutError) as e:
                        rec["status"], rec["error"] = "none", f"{type(e).__name__}: {e}"[:200]
                ok += rec["status"] != "none"
                with LOG.open("a") as f:
                    f.write(json.dumps(rec) + "\n")
                if i % 50 == 0:
                    rate = (time.monotonic() - t0) / i
                    print(f"  {i}/{len(rows)} with news {ok} ({100 * ok / i:.0f}%) "
                          f"eta={(len(rows) - i) * rate / 3600:.1f}h", flush=True)
            print(f"{tag}: news for {ok} of {len(rows)}", flush=True)
    finally:
        await fetcher.aclose()


if __name__ == "__main__":
    asyncio.run(main(SAMPLES_B4 if sys.argv[1:] == ["b4"] else SAMPLES))
