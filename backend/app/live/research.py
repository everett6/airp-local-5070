"""
Live, web-informed research: the jailed agent uses real-time tools to reach a decision.

    python -m app.live.research NVDA AAPL                # 5-day P(up) with sources and a full tool trace
    python -m app.live.research NVDA --rounds 2 --model qwen3:14b

Only valid for decisions made NOW. The same pipeline cannot be backtested
honestly, because today's web already knows what happened after any past
date; its accuracy can only be measured forward in time (Phase B3).

Each decision is saved write-once to results/live/<date>/<ticker>-<time>.json
with the answer, every tool call (arguments, timing, result preview), the
agent's step-by-step plan, and provenance.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.sandbox import provenance as prov
from app.sandbox.jail import AgentJail, JailError, JailLimits
from app.sandbox.walkforward import BACKEND, DATA, RESULTS, OllamaLLM
from app.tools.gateway import TICKER_RE, ToolGateway
from app.tools.netguard import SafeFetcher

LIVE_DIR = RESULTS / "live"
EventFn = Callable[[dict[str, Any]], None]


async def research_one(ticker: str, jail: AgentJail, gateway: ToolGateway, llm: OllamaLLM, *, model: str,
                       horizon: int, max_rounds: int, as_of: datetime) -> dict[str, Any]:
    t0 = time.monotonic()
    calls0 = llm.calls
    overflows0 = getattr(llm, "context_overflows", 0)
    result = await jail.call({
        "task": "research",
        "subject": {"ticker": ticker, "horizon_days": horizon, "as_of": as_of.isoformat(timespec="seconds")},
        "tools": gateway.specs_for_prompt(), "max_rounds": max_rounds, "max_calls_per_round": 6,
        "num_ctx": getattr(llm, "num_ctx", 8192), "num_predict": getattr(llm, "num_predict", 600),
    })
    return {"ticker": ticker, "as_of": as_of.isoformat(timespec="seconds"), "horizon_days": horizon,
            "model": model, **result, "tool_log": gateway.log, "llm_calls": llm.calls - calls0,
            "context_overflows": getattr(llm, "context_overflows", 0) - overflows0,
            "elapsed_s": round(time.monotonic() - t0, 1)}


async def research_tickers(tickers: list[str], *, model: str = "qwen3:8b", horizon: int = 5, max_rounds: int = 3,
                           concurrency: int = 2, on_event: EventFn | None = None, save: bool = True,
                           gateway_factory: Callable[[SafeFetcher, EventFn], ToolGateway] | None = None,
                           llm: Any = None, allow_unjailed: bool = False) -> list[dict[str, Any]]:
    tickers = [t.strip().upper() for t in tickers]
    bad = [t for t in tickers if not TICKER_RE.match(t)]
    if bad:
        raise ValueError(f"invalid tickers: {bad}")
    as_of = datetime.now(UTC)
    llm = llm or OllamaLLM(model, concurrency=4, num_ctx=8192, num_predict=600, cache=False)
    base = ToolGateway.from_env("live")
    fetcher = base.fetcher  # one connection pool and cache shared by every ticker
    provenance = prov.collect(BACKEND.parent, DATA, model)
    limits = JailLimits()
    queue: asyncio.Queue[str] = asyncio.Queue()
    for t in tickers:
        queue.put_nowait(t)
    results: dict[str, dict[str, Any]] = {}

    def emit(ticker: str) -> EventFn:
        def fn(entry: dict[str, Any]) -> None:
            if on_event:
                on_event({"ticker": ticker, **entry})
        return fn

    async def worker() -> None:
        while not queue.empty():
            ticker = queue.get_nowait()
            if gateway_factory:
                gw = gateway_factory(fetcher, emit(ticker))
            else:
                gw = ToolGateway(mode="live", fetcher=fetcher, sec_user_agent=base.sec_user_agent,
                                 brave_api_key=base.brave_api_key, on_event=emit(ticker))
            rec: dict[str, Any] | None = None
            error = ""
            for attempt in range(2):  # one retry: a transient model/network failure shouldn't cost the stock
                try:
                    async with AgentJail(llm, tools=gw, limits=limits, allow_unjailed=allow_unjailed) as jail:
                        rec = await research_one(ticker, jail, gw, llm, model=model, horizon=horizon,
                                                 max_rounds=max_rounds, as_of=as_of)
                    rec |= {"tool_calls": jail.tool_calls, "attempts": attempt + 1}
                    break
                except (JailError, OSError, httpx.HTTPError, KeyError, ValueError) as e:
                    error = f"{type(e).__name__}: {e}"[:500]
            if rec is None:
                # recorded as a failure, never silently turned into a 0.5 forecast
                rec = {"ticker": ticker, "as_of": as_of.isoformat(timespec="seconds"), "horizon_days": horizon,
                       "model": model, "p_up": None, "answered": False, "error": error, "reason": "", "sources": [],
                       "rounds": 0, "tool_calls": 0, "elapsed_s": 0.0, "attempts": 2}
            rec |= {"provenance": provenance, "jail_limits": limits.as_dict()}
            if on_event:
                on_event({"ticker": ticker, "final": True, "p_up": rec["p_up"], "elapsed_s": rec["elapsed_s"],
                          "error": rec.get("error", "")})
            if save:
                rec["saved_to"] = str(save_decision(rec).relative_to(BACKEND))
            results[ticker] = rec

    try:
        await asyncio.gather(*(worker() for _ in range(max(1, min(concurrency, len(tickers))))))
    finally:
        await fetcher.aclose()
    return [results[t] for t in tickers if t in results]


def save_decision(rec: dict[str, Any], root: Path = LIVE_DIR) -> Path:
    stamp = datetime.fromisoformat(rec["as_of"])
    folder = root / stamp.date().isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{rec['ticker']}-{stamp.strftime('%H%M%S')}.json"
    with path.open("x") as f:  # write-once: a decision is never silently replaced
        json.dump(rec, f, indent=1, default=str)
    return path


def _print_event(e: dict[str, Any]) -> None:
    if e.get("final"):
        print(f"[{e['ticker']}] DONE p_up={e['p_up']:.2f} in {e['elapsed_s']}s", flush=True)
        return
    args = json.dumps(e.get("args"))[:80]
    status = "ok" if e["ok"] else f"ERROR {e['error'][:60]}"
    print(f"[{e['ticker']}] {e['tool']}({args}) {e['elapsed_ms']} ms {status}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tickers", nargs="+")
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--rounds", type=int, default=3, help="max tool rounds before the final answer")
    ap.add_argument("--concurrency", type=int, default=2)
    args = ap.parse_args()
    recs = asyncio.run(research_tickers(args.tickers, model=args.model, horizon=args.horizon,
                                        max_rounds=args.rounds, concurrency=args.concurrency, on_event=_print_event))
    for r in recs:
        print(f"\n{r['ticker']}: P(up in {r['horizon_days']}d) = {r['p_up']:.2f}  "
              f"({r['rounds']} rounds, {r['tool_calls']} tool calls, {r['llm_calls']} LLM calls, {r['elapsed_s']}s)")
        print(f"  {r['reason']}")
        for s in r["sources"][:5]:
            print(f"  - {s}")
        print(f"  saved: {r['saved_to']}")


if __name__ == "__main__":
    main()
