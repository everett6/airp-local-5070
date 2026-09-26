"""Research sub-agent on every earnings release: web tools as of the release, a source-checked brief, into the
research table, then Bonsai decides with it.

    python scripts/research_events.py --features results/events/features_sp500_2025.csv --limit 400
    python scripts/decide_events.py --features results/events/features_sp500_2025_research.csv --tag research ...

Per release: the jailed qwen3:8b agent gets the as-of internet tools (app/tools/asof.py: SEC filings and the
release itself, Wikipedia revisions, archived news pages, price history), all limited to what existed at the SEC
acceptance time of the release (entry is the next open, so nothing it reads postdates the decision). It writes a
brief whose facts are checked against the fetched sources (agent_worker.verify_brief); only verified facts are added
to the fact sheet. Results: results/events_research/<accession>.json and features_<name>_research.csv.
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

from app.sandbox.events import Prices
from app.sandbox.gpu_lock import gpu_job
from app.sandbox.jail import AgentJail, JailError, JailLimits
from app.sandbox.walkforward import OllamaLLM
from app.tools.gateway import ToolGateway
from app.tools.netguard import SafeFetcher

OUT = BACKEND / "results" / "events_research"
WEBCACHE = BACKEND / "results" / "webcache"


def lookup_from(p: Prices):
    def fn(ticker: str, as_of: datetime, n: int) -> list[tuple[str, float]]:
        t = ticker.replace(".", "-")
        if t not in p.close.columns:
            return []
        # closes strictly before the as-of day: the release day's close is after the release
        s = p.close[t].loc[: pd.Timestamp(as_of.date()) - pd.Timedelta(days=1)].dropna().tail(n)
        return [(d.date().isoformat(), float(v)) for d, v in s.items()]
    return fn


async def research(r, llm: OllamaLLM, fetcher: SafeFetcher, ua: str, lookup, rounds: int) -> dict:
    as_of = datetime.fromisoformat(str(r.accepted_utc)).replace(tzinfo=UTC)
    gw = ToolGateway(mode="as_of", fetcher=fetcher, as_of=as_of, sec_user_agent=ua, price_lookup=lookup,
                     tool_cache=WEBCACHE, max_result_chars=5000)
    t0 = time.monotonic()
    err = ""
    for _ in range(2):
        try:
            async with AgentJail(llm, tools=gw, limits=JailLimits()) as jail:
                res = await jail.call({
                    "task": "research", "prompt": "as_of", "brief": True,
                    "subject": {"ticker": str(r.ticker), "horizon_days": 20, "as_of": as_of.isoformat()},
                    "tools": gw.specs_for_prompt(), "max_rounds": rounds, "max_calls_per_round": 4,
                    "num_ctx": llm.num_ctx, "num_predict": llm.num_predict})
            return {"accession": r.accession, "ticker": r.ticker, "as_of": as_of.isoformat(), **res,
                    "tool_log": gw.log, "elapsed_s": round(time.monotonic() - t0, 1)}
        except (JailError, OSError) as e:
            err = f"{type(e).__name__}: {e}"[:500]
    return {"accession": r.accession, "ticker": r.ticker, "as_of": as_of.isoformat(), "error": err,
            "tool_log": gw.log, "elapsed_s": round(time.monotonic() - t0, 1)}


def brief_lines(rec: dict) -> list[str]:
    b = rec.get("brief") or {}
    facts = [f for f in b.get("facts", []) if isinstance(f, dict) and f.get("text")]
    if not facts:
        return []
    out = ["Research agent (web sources as of the release, each fact checked against its source):"]
    out += [f"  - {str(f['text'])[:300]} ({str(f.get('date', ''))[:10]})" for f in facts[:6]]
    for key, label in (("catalysts", "Upcoming"), ("risks", "Risks")):
        items = [str(x)[:200] for x in b.get(key, []) if x][:3]
        if items:
            out.append(f"{label}: " + "; ".join(items))
    return out


async def run(args: argparse.Namespace) -> None:
    feats = pd.read_csv(BACKEND / args.features)
    if args.limit:
        feats = feats.head(args.limit)  # the event files are shuffled with a fixed seed: a random sample
    p = Prices.from_long(pd.read_parquet(BACKEND / args.prices))
    OUT.mkdir(parents=True, exist_ok=True)
    llm = OllamaLLM(args.model, base_url=args.base_url, concurrency=args.workers, num_ctx=8192, num_predict=1200,
                    cache=True, require_gpu=True)
    base = ToolGateway.from_env("as_of", as_of=datetime(2000, 1, 1, tzinfo=UTC))
    if not base.sec_user_agent:
        raise SystemExit("set SEC_USER_AGENT in backend/.env")
    fetcher = SafeFetcher(base.fetcher.user_agent, timeout_s=40.0, total_timeout_s=80.0)
    ua = base.sec_user_agent
    await base.aclose()
    lookup = lookup_from(p)
    todo = [r for r in feats.itertuples() if not (OUT / f"{r.accession}.json").exists()]
    print(f"{len(feats)} releases, {len(todo)} to research, {args.workers} at a time", flush=True)
    queue: asyncio.Queue = asyncio.Queue()
    for r in todo:
        queue.put_nowait(r)
    done, t0 = 0, time.monotonic()

    async def worker() -> None:
        nonlocal done
        while not queue.empty():
            r = queue.get_nowait()
            rec = await research(r, llm, fetcher, ua, lookup, args.rounds)
            (OUT / f"{r.accession}.json").write_text(json.dumps(rec, indent=1, default=str) + "\n")
            done += 1
            rate = (time.monotonic() - t0) / done
            nf = len((rec.get("brief") or {}).get("facts", []))
            print(f"[{done}/{len(todo)}] {r.ticker:6s} facts={nf} tools={len(rec.get('tool_log', []))} "
                  f"ok={sum(e['ok'] for e in rec.get('tool_log', []))} {rec['elapsed_s']}s "
                  f"{rate:.1f}s/release eta={(len(todo) - done) * rate / 3600:.1f}h", flush=True)
    try:
        await asyncio.gather(*(worker() for _ in range(args.workers)))
    finally:
        await fetcher.aclose()
        await llm.unload()
    rows = []
    for r in feats.itertuples():
        path = OUT / f"{r.accession}.json"
        extra = brief_lines(json.loads(path.read_text())) if path.exists() else []
        rows.append({**r._asdict(), "research_facts": max(0, len(extra) - 1),
                     "fact_sheet": r.fact_sheet + ("\n" + "\n".join(extra) if extra else "")})
    out = BACKEND / args.features.replace(".csv", "_research.csv")
    df = pd.DataFrame(rows).drop(columns=["Index"])
    df.to_csv(out, index=False)
    print(f"{out.name}: {len(df)} releases, {int((df['research_facts'] > 0).sum())} with verified research facts")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--features", default="results/events/features_sp500_2025.csv")
    ap.add_argument("--prices", default="data/events/ohlcv_2023-01-01_2026-09-25.parquet")
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--base-url", default="http://127.0.0.1:11437")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    with gpu_job("research_events"):
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
