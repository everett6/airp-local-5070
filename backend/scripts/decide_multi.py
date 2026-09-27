"""Bonsai's BUY/PASS for several books per release, with the fact sheet read once (prefix caching).

    python scripts/decide_multi.py --features results/events/features_sp500_2025_secchk.csv --tag factsheet_secchk \
        --horizons 5,20,63,120,252 --limit 200

decide_events.py puts the holding period in the system prompt, ahead of the fact sheet, so each book re-reads the
whole sheet. Here the system prompt names no period and the period is the LAST line of the user message; one
release's books are asked back to back, so the server's KV cache holds the sheet and each further book only
processes the final line. The prompt differs from decide_events.py, so the output is a separate arm
(results/events/decide_<model>_<tag>_hl[_h<N>].jsonl, same row format; horizons6_eval.py reads it with tag
<tag>_hl). Compare it with the old prompt before adopting it: both arms of a test must use the same prompt.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import pandas as pd

from app.sandbox.gpu_lock import gpu_job
from app.sandbox.walkforward import OllamaLLM

SYSTEM = ("You are a portfolio manager reacting to an earnings release. Buy only stocks you expect to beat their "
          "sector over the holding period stated at the end. Use only the facts given. "
          "Answer with exactly one word: BUY or PASS.")


def user_prompt(sheet: str, h: int) -> str:
    return f"{sheet}\n\nHolding period: the next {h} trading days after this release."


def out_path(model: str, tag: str, h: int) -> Path:
    slug = model.replace(":", "_").replace("/", "_") + f"_{tag}_hl" + ("" if h == 20 else f"_h{h}")
    return BACKEND / "results" / "events" / f"decide_{slug}.jsonl"


async def run(args: argparse.Namespace) -> None:
    horizons = [int(x) for x in args.horizons.split(",")]
    feats = pd.read_csv(BACKEND / args.features)
    if args.limit:
        feats = feats.head(args.limit)
    done = {h: ({json.loads(x)["accession"] for x in out_path(args.model, args.tag, h).read_text().splitlines()}
                if out_path(args.model, args.tag, h).exists() else set()) for h in horizons}
    todo = [r for r in feats.itertuples() if any(r.accession not in done[h] for h in horizons)]
    print(f"{len(todo)} releases x {len(horizons)} books with {args.model}", flush=True)
    llm = OllamaLLM(args.model, base_url=args.base_url, concurrency=args.parallel, num_ctx=4096, num_predict=400,
                    cache=True, require_gpu=True)
    files = {h: out_path(args.model, args.tag, h).open("a") for h in horizons}
    queue: asyncio.Queue = asyncio.Queue()
    for r in todo:
        queue.put_nowait(r)
    n, t0 = 0, time.monotonic()

    async def worker() -> None:
        nonlocal n
        while not queue.empty():
            r = queue.get_nowait()
            for h in horizons:  # back to back: the sheet is still in this slot's KV cache
                if r.accession in done[h]:
                    continue
                got = json.loads(await llm(SYSTEM, user_prompt(r.fact_sheet, h), mode="buypass_lo"))
                lo = float(got["logodds"])
                files[h].write(json.dumps({
                    "accession": r.accession, "ticker": r.ticker, "model": args.model, "logodds": lo,
                    "p_buy": 1.0 / (1.0 + math.exp(-max(-50.0, min(50.0, lo)))), "mass": got["mass"],
                    "censored": got["censored"], "buy": lo > 0, "horizon": h}) + "\n")
                files[h].flush()
            n += 1
            if n % 100 == 0:
                rate = (time.monotonic() - t0) / n
                print(f"  {n}/{len(todo)} {rate:.2f}s/release ({rate / len(horizons):.2f}s/decision) "
                      f"eta={(len(todo) - n) * rate / 60:.0f}min", flush=True)

    try:
        await asyncio.gather(*(worker() for _ in range(args.parallel)))
    finally:
        for f in files.values():
            f.close()
        await llm.unload()
    rate = (time.monotonic() - t0) / max(1, n)
    print(f"{n} releases, {rate:.2f}s/release ({rate / len(horizons):.2f}s/decision)", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="bonsai-27b:latest")
    ap.add_argument("--base-url", default="http://127.0.0.1:11435")
    ap.add_argument("--features", required=True, help="table with accession, ticker, fact_sheet")
    ap.add_argument("--tag", required=True, help="output tag; '_hl' is appended")
    ap.add_argument("--horizons", default="5,20,63,120,252")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--parallel", type=int, default=3, help="releases in flight (the Ollama server's slots)")
    args = ap.parse_args()
    with gpu_job(f"decide_multi {args.model}"):
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
