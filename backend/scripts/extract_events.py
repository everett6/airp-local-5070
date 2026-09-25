"""Stage "reader": turn each earnings press release into checked numbers.

    python scripts/extract_events.py fetch   --events data/events/events_2024-01-01_2026-09-24.csv   # network, no GPU
    python scripts/extract_events.py extract --events data/events/events_2024-01-01_2026-09-24.csv --from 2025-01-01

fetch    downloads each EX-99 press release (SEC, 5 requests/s) to data/events/text/<accession>.txt.gz (plain text,
         not committed: rebuildable, and large).
extract  asks a small model (default qwen3:8b) for revenue, diluted EPS (GAAP and adjusted) for the quarter and a
         year earlier, the guidance change and the tone, each number with an exact quote. Code keeps a number
         only if its quote is word-for-word in the release AND contains the number (at the stated scale:
         millions written as "$3.4 billion" counts). Results: results/events/extract_<model>.jsonl, one line per
         event, replayable from the LLM cache.
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import sys
import time
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import pandas as pd

from app.sandbox.events import check
from app.sandbox.gpu_lock import gpu_job
from app.sandbox.walkforward import OllamaLLM
from app.tools.extract import html_to_text
from app.tools.gateway import _read_env_file

TEXT = BACKEND / "data" / "events" / "text"
OUT = BACKEND / "results" / "events"
MAX_CHARS = 14000  # first part of the release: headline numbers, guidance, and the income-statement table

READER_SYSTEM = """You read one quarterly earnings press release. Extract, using ONLY the release:
- revenue for the reported quarter and the same quarter a year earlier, in millions of dollars;
- diluted EPS (GAAP) for the quarter and a year earlier; adjusted/non-GAAP EPS if the release reports it;
- guidance: "raised", "lowered", "maintained", "initiated", "withdrawn" or "none" (not mentioned);
- tone: "positive", "neutral" or "negative" (management's own words).
For every number give an exact quote from the release that contains it (copy it character for character, max 25
words). Use null when the release does not state a number. Never compute or estimate a number.

Reply with ONLY this JSON:
{"period_end": "YYYY-MM-DD or null",
 "revenue": {"q": number|null, "prior": number|null, "quote": "..."},
 "eps": {"q": number|null, "prior": number|null, "quote": "..."},
 "adj_eps": {"q": number|null, "prior": number|null, "quote": "..."},
 "guidance": "...", "guidance_quote": "...", "tone": "...", "highlights": ["<exact quote>", "<exact quote>"]}"""


def text_path(accession: str) -> Path:
    return TEXT / f"{accession}.txt.gz"


async def fetch_all(args: argparse.Namespace) -> None:
    sys.path.insert(0, str(BACKEND / "scripts"))
    from build_events import Sec

    ev = pd.read_csv(BACKEND / args.events)
    ev = ev[(ev["ex99_url"].fillna("") != "") & (ev["filed"] >= args.date_from)]
    TEXT.mkdir(parents=True, exist_ok=True)
    todo = [r for r in ev.itertuples() if not text_path(r.accession).exists()]
    print(f"{len(ev)} events with a press release, {len(todo)} to download", flush=True)
    sec = Sec(_read_env_file(BACKEND / ".env")["SEC_USER_AGENT"])
    sem = asyncio.Semaphore(4)
    done = 0
    t0 = time.monotonic()

    async def one(r: Any) -> None:
        nonlocal done
        async with sem:
            resp = await sec.get(r.ex99_url)
        if resp is not None:
            page = html_to_text(resp.text, 200_000)["text"] if "htm" in r.ex99_url.lower() else resp.text
            text_path(r.accession).write_bytes(gzip.compress(page.encode()))
        done += 1
        if done % 500 == 0:
            print(f"  {done}/{len(todo)} {time.monotonic() - t0:.0f}s", flush=True)
    for i in range(0, len(todo), 200):
        await asyncio.gather(*(one(r) for r in todo[i:i + 200]))
    await sec.client.aclose()
    print("fetch done", flush=True)


async def extract(args: argparse.Namespace) -> None:
    ev = pd.read_csv(BACKEND / args.events)
    ev = ev[(ev["filed"] >= args.date_from) & (ev["filed"] <= args.date_to)]
    OUT.mkdir(parents=True, exist_ok=True)
    slug = args.model.replace(":", "_").replace("/", "_")
    out_path = OUT / f"extract_{slug}.jsonl"
    done = {json.loads(x)["accession"] for x in out_path.read_text().splitlines()} if out_path.exists() else set()
    todo = [r for r in ev.itertuples() if r.accession not in done and text_path(r.accession).exists()]
    print(f"{len(ev)} events in window, {len(done)} extracted, {len(todo)} to go with {args.model}", flush=True)
    llm = OllamaLLM(args.model, base_url=args.base_url, concurrency=1, num_ctx=8192, num_predict=450, cache=True,
                    require_gpu=True)
    t0 = time.monotonic()
    try:
        await _extract_loop(todo, llm, out_path, t0, args)
    finally:
        await llm.unload()


async def _extract_loop(todo: list[Any], llm: OllamaLLM, out_path: Path, t0: float, args: argparse.Namespace) -> None:
    with out_path.open("a") as f:
        for n, r in enumerate(todo, start=1):
            text = gzip.decompress(text_path(r.accession).read_bytes()).decode()
            raw_text = await llm(READER_SYSTEM, text[:MAX_CHARS])
            try:
                raw = json.loads(raw_text[raw_text.index("{"): raw_text.rindex("}") + 1])
            except ValueError:
                raw = None
            rec = {"accession": r.accession, "ticker": r.ticker, "cik": int(r.cik), "accepted_utc": r.accepted_utc,
                   "model": args.model, **check(raw, text[:MAX_CHARS])}
            f.write(json.dumps(rec) + "\n")
            f.flush()
            if n % 50 == 0:
                rate = (time.monotonic() - t0) / n
                print(f"  {n}/{len(todo)} {rate:.1f}s/event eta={(len(todo) - n) * rate / 3600:.1f}h", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("fetch", "extract"))
    ap.add_argument("--events", default="data/events/events_2024-01-01_2026-09-24.csv")
    ap.add_argument("--from", dest="date_from", default="2024-01-01")
    ap.add_argument("--to", dest="date_to", default="2026-09-24")
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--base-url", default=None)
    args = ap.parse_args()
    if args.cmd == "fetch":
        asyncio.run(fetch_all(args))
    else:
        with gpu_job(f"extract_events {args.model}"):
            asyncio.run(extract(args))


if __name__ == "__main__":
    main()
