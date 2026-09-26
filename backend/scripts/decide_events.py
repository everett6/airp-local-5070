"""Stage "decide" for earnings events: the decision model says BUY or PASS on each release.

    python scripts/decide_events.py --model bonsai-27b:latest --base-url http://127.0.0.1:11435 --from 2025-01-01

Input per event (all known before the entry open; nothing the model computes):
  - the reader's CHECKED numbers (each quoted word-for-word from the release): revenue and diluted EPS vs a year
    earlier, with growth rates computed here; guidance change; tone; up to 3 verified quotes;
  - price context computed from closes before the release: 20- and 60-day return vs the sector, 12-1 momentum.
The model answers one word, BUY or PASS; the decision and its strength are read from token log-probabilities
(log P(BUY) - log P(PASS), unrounded). BUY means log-odds > 0. A sample (--explain N) also gets a written bull/bear
case for human review. Results: results/events/decide_<model>[_<tag>].jsonl (replayable from the LLM cache).
With --features, the model reads the research table's fact sheet instead (reader + SEC tool + prices).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import pandas as pd

from app.sandbox.events import Prices, entry_index
from app.sandbox.gpu_lock import gpu_job
from app.sandbox.walkforward import OllamaLLM

SECTOR_ETF = {"Information Technology": "XLK", "Financials": "XLF", "Health Care": "XLV",
              "Consumer Discretionary": "XLY", "Consumer Staples": "XLP", "Energy": "XLE", "Industrials": "XLI",
              "Materials": "XLB", "Utilities": "XLU", "Real Estate": "XLRE", "Communication Services": "XLC"}

DECIDE_SYSTEM = ("You are a portfolio manager reacting to an earnings release. Buy only stocks you expect to beat "
                 "their sector over the next 20 trading days after this release. Use only the facts given. "
                 "Answer with exactly one word: BUY or PASS.")
# the three books: quick money (a week), mid term (a month), long term (about six months)
HORIZONS = {5: "quick", 20: "mid", 120: "long"}
EXPLAIN_SYSTEM = ("You are a portfolio manager reacting to an earnings release. Using only the facts given, reply "
                  "with ONLY JSON: {\"bull\": [\"...\"], \"bear\": [\"...\"], \"decision\": \"BUY\" or \"PASS\", "
                  "\"reason\": \"1-2 sentences\"}")


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:+.1f}%"


def event_text(ev: Any, ex: dict[str, Any], p: Prices, i: int, etf: str) -> str:
    t = str(ev.ticker).replace(".", "-")

    def rel(n: int) -> float | None:
        if i - 1 - n < 0:
            return None
        c, e = p.close[t], p.close[etf]
        a, b, x, y = c.iloc[i - 1 - n], c.iloc[i - 1], e.iloc[i - 1 - n], e.iloc[i - 1]
        return None if any(pd.isna(v) for v in (a, b, x, y)) else float((b / a - 1) - (y / x - 1))

    def growth(d: dict[str, float], money: bool) -> str:
        if "q" not in d:
            return "not stated"
        s = f"{d['q']:,.0f}M" if money else f"{d['q']:.2f}"
        if d.get("prior"):
            prior = f"{d['prior']:,.0f}M" if money else f"{d['prior']:.2f}"
            s += f" vs {prior} a year earlier ({pct(d['q'] / d['prior'] - 1) if money else pct((d['q'] - d['prior']) / abs(d['prior']))})"
        return s
    lines = [f"Company: {ev.ticker} ({ev.sector}); earnings release filed {str(ev.accepted_utc)[:16]} UTC.",
             f"Revenue: {growth(ex.get('revenue') or {}, True)}",
             f"Diluted EPS (GAAP): {growth(ex.get('eps') or {}, False)}",
             f"Adjusted EPS: {growth(ex.get('adj_eps') or {}, False)}",
             f"Guidance: {ex.get('guidance', 'none')}; management tone: {ex.get('tone', 'neutral')}"]
    lines += [f'Quote: "{q}"' for q in ex.get("highlights", [])]
    lines.append(f"Stock vs its sector before the release: 20 days {pct(rel(20))}, 60 days {pct(rel(60))}")
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> None:
    ev = pd.read_csv(BACKEND / args.events)
    ev = ev[(ev["filed"] >= args.date_from) & (ev["filed"] <= args.date_to)]
    ex = {json.loads(x)["accession"]: json.loads(x) for x in (BACKEND / args.extract).read_text().splitlines()}
    p = Prices.from_long(pd.read_parquet(BACKEND / args.prices))
    days = pd.DatetimeIndex(p.open.index)
    slug = (args.model.replace(":", "_").replace("/", "_") + (f"_{args.tag}" if args.tag else "")
            + ("" if args.horizon == 20 else f"_h{args.horizon}"))
    sheets = (pd.read_csv(BACKEND / args.features).set_index("accession")["fact_sheet"].to_dict()
              if args.features else {})
    out_path = BACKEND / "results" / "events" / f"decide_{slug}.jsonl"
    done = {json.loads(x)["accession"] for x in out_path.read_text().splitlines()} if out_path.exists() else set()
    todo = [r for r in ev.itertuples() if r.accession in ex and r.accession not in done
            and (not sheets or r.accession in sheets)]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(todo)} events to decide with {args.model} ({len(done)} done)", flush=True)
    llm = OllamaLLM(args.model, base_url=args.base_url, concurrency=args.parallel, num_ctx=4096, num_predict=400, cache=True,
                    require_gpu=True)
    t0 = time.monotonic()
    system = DECIDE_SYSTEM.replace("next 20 trading days", f"next {args.horizon} trading days")

    async def one(n: int, r: Any) -> dict[str, Any] | None:
        etf = SECTOR_ETF.get(str(r.sector))
        i = entry_index(days, datetime.fromisoformat(str(r.accepted_utc)))
        if etf is None or i is None or str(r.ticker).replace(".", "-") not in p.close.columns:
            return None
        user = sheets[r.accession] if sheets else event_text(r, ex[r.accession], p, i, etf)
        got = json.loads(await llm(system, user, mode="buypass_lo"))
        lo = float(got["logodds"])
        # p_buy: the model's own probability of answering BUY rather than PASS (from its token probabilities).
        # The master agent turns it into a calibrated P(beats sector) using only events whose outcome was
        # already known (app/portfolio/master.py).
        rec = {"accession": r.accession, "ticker": r.ticker, "model": args.model, "logodds": lo,
               "p_buy": 1.0 / (1.0 + math.exp(-max(-50.0, min(50.0, lo)))),
               "mass": got["mass"], "censored": got["censored"], "buy": lo > 0}
        if n <= args.explain:
            rec["explanation"] = (await llm(EXPLAIN_SYSTEM, user))[:2000]
        rec["prompt_user"] = user
        return rec

    try:
        with out_path.open("a") as f:
            for c in range(0, len(todo), args.parallel):  # one-token answers: several in flight fill the GPU
                chunk = list(enumerate(todo[c:c + args.parallel], start=c + 1))
                for rec in await asyncio.gather(*(one(n, r) for n, r in chunk)):
                    if rec is not None:
                        f.write(json.dumps(rec) + "\n")
                f.flush()
                n = c + len(chunk)
                if n // 100 != c // 100:
                    rate = (time.monotonic() - t0) / n
                    print(f"  {n}/{len(todo)} {rate:.2f}s/event eta={(len(todo) - n) * rate / 60:.0f}min", flush=True)
    finally:
        await llm.unload()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="bonsai-27b:latest")
    ap.add_argument("--base-url", default="http://127.0.0.1:11435")
    ap.add_argument("--events", default="data/events/events_2024-01-01_2026-09-24.csv")
    ap.add_argument("--extract", default="results/events/extract_qwen3_8b.jsonl")
    ap.add_argument("--prices", default="data/events/ohlcv_2023-01-01_2026-09-25.parquet")
    ap.add_argument("--from", dest="date_from", default="2025-01-01")
    ap.add_argument("--to", dest="date_to", default="2026-09-24")
    ap.add_argument("--features", default=None,
                    help="research table from build_features.py: the model reads its fact_sheet column")
    ap.add_argument("--tag", default="", help="suffix for the output file (e.g. xbrl)")
    ap.add_argument("--horizon", type=int, default=20, choices=sorted(HORIZONS),
                    help="5 quick money, 20 mid term, 120 long term (trading days to beat the sector)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--parallel", type=int, default=3, help="requests in flight (the Ollama server's slots)")
    ap.add_argument("--explain", type=int, default=100, help="write a bull/bear case for the first N events")
    args = ap.parse_args()
    with gpu_job(f"decide_events {args.model}"):
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
