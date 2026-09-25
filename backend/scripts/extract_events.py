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


NUEXTRACT_TEMPLATE = {
    "period_end": "date",
    "revenue_unit": ["thousands", "millions", "billions"],
    "revenue_current_quarter": "number", "revenue_same_quarter_prior_year": "number",
    "revenue_sentence": "verbatim-string",
    "diluted_eps_current_quarter": "number", "diluted_eps_same_quarter_prior_year": "number",
    "diluted_eps_sentence": "verbatim-string",
    "adjusted_eps_current_quarter": "number", "adjusted_eps_same_quarter_prior_year": "number",
    "adjusted_eps_sentence": "verbatim-string",
    "guidance": ["raised", "lowered", "maintained", "initiated", "withdrawn", "none"],
    "guidance_sentence": "verbatim-string",
    "management_tone": ["positive", "neutral", "negative"],
    "key_sentences": ["verbatim-string"],
}
SCALE = {"thousands": 0.001, "millions": 1.0, "billions": 1000.0}


def nuextract_prompt(text: str) -> str:
    return f"# Template:\n{json.dumps(NUEXTRACT_TEMPLATE, indent=1)}\n# Context:\n{text}"


def from_nuextract(o: dict[str, Any] | None) -> dict[str, Any] | None:
    """NuExtract's template output -> the reader schema `check` verifies (revenue in millions)."""
    if not o:
        return None
    k = SCALE.get(str(o.get("revenue_unit")), 1.0)

    def num(x: Any, scale: float = 1.0) -> float | None:
        return float(x) * scale if isinstance(x, int | float) and not isinstance(x, bool) else None
    return {"period_end": o.get("period_end"),
            "revenue": {"q": num(o.get("revenue_current_quarter"), k),
                        "prior": num(o.get("revenue_same_quarter_prior_year"), k), "quote": o.get("revenue_sentence")},
            "eps": {"q": num(o.get("diluted_eps_current_quarter")), "prior": num(o.get(
                "diluted_eps_same_quarter_prior_year")), "quote": o.get("diluted_eps_sentence")},
            "adj_eps": {"q": num(o.get("adjusted_eps_current_quarter")), "prior": num(o.get(
                "adjusted_eps_same_quarter_prior_year")), "quote": o.get("adjusted_eps_sentence")},
            "guidance": o.get("guidance") or "none", "guidance_quote": o.get("guidance_sentence"),
            "tone": o.get("management_tone") or "neutral", "highlights": o.get("key_sentences") or []}


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
    slug = args.model.split("/")[-1].replace(":", "_").replace("/", "_")
    out_path = BACKEND / args.out if args.out else OUT / f"extract_{slug}.jsonl"
    done = {json.loads(x)["accession"] for x in out_path.read_text().splitlines()} if out_path.exists() else set()
    todo = [r for r in ev.itertuples() if r.accession not in done and text_path(r.accession).exists()]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(ev)} events in window, {len(done)} extracted, {len(todo)} to go with {args.model}", flush=True)
    # extraction is checked number-by-number by code, so batched decoding (--parallel, needs an Ollama server
    # started with OLLAMA_NUM_PARALLEL >= that) is allowed here even though it can shift a token now and then
    llm = OllamaLLM(args.model, base_url=args.base_url, concurrency=args.parallel, num_ctx=8192, num_predict=450,
                    cache=True, require_gpu=True)
    t0 = time.monotonic()
    try:
        await _extract_loop(todo, llm, out_path, t0, args)
    finally:
        await llm.unload()


async def _extract_loop(todo: list[Any], llm: OllamaLLM, out_path: Path, t0: float, args: argparse.Namespace) -> None:
    nu = "nuextract" in args.model.lower()
    n = 0
    with out_path.open("a") as f:
        async def one(r: Any) -> None:
            nonlocal n
            text = gzip.decompress(text_path(r.accession).read_bytes()).decode()[: args.max_chars]
            raw_text = await (llm("", nuextract_prompt(text)) if nu else llm(READER_SYSTEM, text))
            try:
                raw = json.loads(raw_text[raw_text.index("{"): raw_text.rindex("}") + 1])
            except ValueError:
                raw = None
            rec = {"accession": r.accession, "ticker": r.ticker, "cik": int(r.cik), "accepted_utc": r.accepted_utc,
                   "model": args.model, **check(from_nuextract(raw) if nu else raw, text)}
            f.write(json.dumps(rec) + "\n")
            f.flush()
            n += 1
            if n % 50 == 0:
                rate = (time.monotonic() - t0) / n
                print(f"  {n}/{len(todo)} {rate:.2f}s/event eta={(len(todo) - n) * rate / 3600:.1f}h", flush=True)
        # a bounded pool: the Ollama client's own semaphore (concurrency=--parallel) sets how many run at once
        for i in range(0, len(todo), 64):
            await asyncio.gather(*(one(r) for r in todo[i:i + 64]))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("fetch", "extract"))
    ap.add_argument("--events", default="data/events/events_2024-01-01_2026-09-24.csv")
    ap.add_argument("--from", dest="date_from", default="2024-01-01")
    ap.add_argument("--to", dest="date_to", default="2026-09-24")
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--parallel", type=int, default=1, help="requests in flight (Ollama OLLAMA_NUM_PARALLEL)")
    ap.add_argument("--max-chars", type=int, default=MAX_CHARS)
    ap.add_argument("--limit", type=int, default=0, help="only the first N events (benchmarks)")
    ap.add_argument("--out", default=None, help="output jsonl (default results/events/extract_<model>.jsonl)")
    args = ap.parse_args()
    if args.cmd == "fetch":
        asyncio.run(fetch_all(args))
    else:
        with gpu_job(f"extract_events {args.model}"):
            asyncio.run(extract(args))


if __name__ == "__main__":
    main()
