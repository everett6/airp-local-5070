"""Research sub-agent on every earnings release: web tools as of the release, a source-checked brief, into the
research table, then Bonsai decides with it.

    python scripts/research_events.py --features results/events/features_sp500_2025.csv --limit 400
    python scripts/decide_events.py --features results/events/features_sp500_2025_research.csv --tag research ...

Per release: the jailed research agent (default Jan-v1-4B with native tool calls; Bonsai-27B writes the brief) gets the as-of internet tools (app/tools/asof.py: SEC filings and the
release itself, Wikipedia revisions, archived news pages, price history), all limited to what existed at the SEC
acceptance time of the release (entry is the next open, so nothing it reads postdates the decision). It writes a
brief whose facts are checked against the fetched sources (agent_worker.verify_brief); only verified facts are added
to the fact sheet. Results: results/events_research_<model><run-tag>/<accession>.json and
features_<name>_research_<model><run-tag>.csv (researched releases only).
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

from app.sandbox.agent_worker import BRIEF_SYSTEM
from app.sandbox.events import Prices
from app.sandbox.gpu_lock import gpu_job
from app.sandbox.jail import AgentJail, JailError, JailLimits
from app.sandbox.vllm_client import VLLMChat
from app.sandbox.walkforward import OllamaLLM
from app.tools.gateway import ToolGateway
from app.tools.netguard import SafeFetcher

OUT = BACKEND / "results" / "events_research"  # + "_<model>"


class Router:
    """Research rounds go to the tool-using model (Jan); the brief (summarize + cite, then code-checked) goes to the
    writer model (Bonsai). Both stay loaded together (Jan on vLLM or Ollama, Bonsai on Ollama; ~10 GB)."""

    def __init__(self, research: OllamaLLM | VLLMChat, writer: OllamaLLM | None) -> None:
        self.research, self.writer = research, writer
        self.num_ctx, self.num_predict = research.num_ctx, research.num_predict

    async def __call__(self, system: str, user: str, mode: str | None = None) -> str:
        llm = self.writer if self.writer is not None and system.startswith(BRIEF_SYSTEM[:60]) else self.research
        return await (llm(system, user, mode=mode) if mode else llm(system, user))


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


def prefetch_for(r, ex99: dict[str, str], names: dict[int, str]) -> list[dict]:
    """The routine first look, fetched in parallel by code before the model's first round."""
    t = str(r.ticker)
    calls = [{"tool": "price_history_as_of", "args": {"ticker": t, "days": 60}},
             {"tool": "sec_filings_as_of", "args": {"ticker": t, "forms": ["8-K", "10-Q", "10-K"], "limit": 5}},
             {"tool": "news_as_of", "args": {"ticker": t}}]
    if ex99.get(r.accession):  # the earnings release itself
        calls.append({"tool": "read_filing", "args": {"url": ex99[r.accession], "max_chars": 4000}})
    if names.get(int(r.cik)):
        calls.append({"tool": "wiki_as_of", "args": {"title": names[int(r.cik)], "max_chars": 1500}})
    return calls


async def research(r, llm: Router, fetcher: SafeFetcher, ua: str, lookup, rounds: int,
                   prefetch: list[dict] | None = None) -> dict:
    as_of = datetime.fromisoformat(str(r.accepted_utc)).replace(tzinfo=UTC)
    gw = ToolGateway(mode="as_of", fetcher=fetcher, as_of=as_of, sec_user_agent=ua, price_lookup=lookup,
                     tool_cache=WEBCACHE, max_result_chars=5000, timeout_cap_s=25.0)
    t0 = time.monotonic()
    err = ""
    for _ in range(2):
        try:
            async with AgentJail(llm, tools=gw, limits=JailLimits()) as jail:
                res = await jail.call({
                    "task": "research", "prompt": "as_of", "brief": True, "skip_final": True,
                    "prefetch": prefetch or [],
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
    global OUT
    OUT = OUT.with_name(OUT.name + "_" + args.model.split("/")[-1].replace(":", "_") + args.run_tag)
    OUT.mkdir(parents=True, exist_ok=True)
    llm: OllamaLLM | VLLMChat
    if args.backend == "vllm":  # same Jan weights served by vLLM (scripts/vllm_serve.sh); results go to the same folder
        llm = VLLMChat(args.vllm_model, base_url=args.vllm_url, concurrency=2 * args.workers)
    else:
        llm = OllamaLLM(args.model, base_url=args.base_url, concurrency=args.workers, num_ctx=8192,
                        num_predict=1200, cache=True, require_gpu=True)
    base = ToolGateway.from_env("as_of", as_of=datetime(2000, 1, 1, tzinfo=UTC))
    if not base.sec_user_agent:
        raise SystemExit("set SEC_USER_AGENT in backend/.env")
    fetcher = SafeFetcher(base.fetcher.user_agent, timeout_s=40.0, total_timeout_s=80.0)
    ua = base.sec_user_agent
    if args.native_tools:
        llm.native_tools = base.specs_native()
    writer = (OllamaLLM(args.brief_model, base_url=args.brief_base_url, concurrency=1, num_ctx=8192,
                        num_predict=1200, cache=True, require_gpu=True) if args.brief_model else None)
    router = Router(llm, writer)
    await base.aclose()
    lookup = lookup_from(p)
    ev = pd.read_csv(BACKEND / args.events)
    ex99 = {a: u for a, u in zip(ev["accession"], ev["ex99_url"].fillna(""), strict=True) if u}
    mem = pd.read_csv(BACKEND / args.members)
    names = {int(c): str(n) for c, n in zip(mem["cik"], mem["name"], strict=True)}
    todo = [r for r in feats.itertuples() if not (OUT / f"{r.accession}.json").exists()]
    deadline = datetime.fromisoformat(args.deadline).timestamp() if args.deadline else 0.0
    print(f"{len(feats)} releases, {len(todo)} to research, {args.workers} at a time", flush=True)
    queue: asyncio.Queue = asyncio.Queue()
    for r in todo:
        queue.put_nowait(r)
    done, t0 = 0, time.monotonic()

    async def worker() -> None:
        nonlocal done
        while not queue.empty():
            if deadline and time.time() > deadline:
                return  # time budget used: stop taking new releases (resumable; the table below still gets written)
            r = queue.get_nowait()
            pre = prefetch_for(r, ex99, names) if args.prefetch else None
            rec = await research(r, router, fetcher, ua, lookup, args.rounds, pre)
            rec["models"] = {"research": args.model, "brief": args.brief_model or args.model}
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
        if writer is not None:
            await writer.unload()
    rows = []
    for r in feats.itertuples():
        path = OUT / f"{r.accession}.json"
        if not path.exists():
            continue  # only researched releases: the table is the "with research" arm
        extra = brief_lines(json.loads(path.read_text()))
        rows.append({**r._asdict(), "research_facts": max(0, len(extra) - 1),
                     "fact_sheet": r.fact_sheet + ("\n" + "\n".join(extra) if extra else "")})
    out = BACKEND / args.features.replace(".csv", f"_research_{OUT.name.split('_', 2)[-1]}.csv")
    df = pd.DataFrame(rows).drop(columns=["Index"])
    df.to_csv(out, index=False)
    print(f"{out.name}: {len(df)} releases, {int((df['research_facts'] > 0).sum())} with verified research facts")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--features", default="results/events/features_sp500_2025.csv")
    ap.add_argument("--prices", default="data/events/ohlcv_2023-01-01_2026-09-25.parquet")
    ap.add_argument("--model", default="hf.co/janhq/Jan-v1-4B-GGUF:Q4_K_M")
    ap.add_argument("--base-url", default="http://127.0.0.1:11436")
    ap.add_argument("--native-tools", action=argparse.BooleanOptionalAction, default=True,
                    help="use the model's own tool-call format (Jan-v1 is trained on it)")
    ap.add_argument("--brief-model", default="bonsai-27b:latest", help="writes the brief; '' = the research model")
    ap.add_argument("--brief-base-url", default="http://127.0.0.1:11435")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--deadline", default="", help="local time (YYYY-MM-DDTHH:MM) after which no new release starts")
    ap.add_argument("--backend", choices=("ollama", "vllm"), default="ollama")
    ap.add_argument("--vllm-url", default="http://127.0.0.1:8000")
    ap.add_argument("--vllm-model", default="jan-v1-4b", help="served model name (scripts/vllm_serve.sh)")
    ap.add_argument("--rounds", type=int, default=2, help="model rounds after the prefetched first look")
    ap.add_argument("--prefetch", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--run-tag", default="_v2", help="suffix of the results folder")
    ap.add_argument("--events", default="data/events/events_sp500_2025.csv", help="for the press-release URLs")
    ap.add_argument("--members", default="data/events/members_2024_2026.csv", help="for company names")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    with gpu_job("research_events"):
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
