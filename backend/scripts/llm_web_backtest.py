"""Backtest the LLM that researches stocks on the internet, with every lookup limited to its decision date.

    python scripts/llm_web_backtest.py --from 2025-01-01 --to 2026-08-31          # the clean window first
    python scripts/llm_web_backtest.py --from 2011-01-01 --to 2024-12-31 --every 3  # older years, sampled

Design (two stages, as desks do it):
  1. Screen: on each monthly decision day, the `--candidates` stocks (default 20) of that year's point-in-time
     top 100 with the best anomaly_rank score (no model, no web).
  2. Research: the jailed LLM researches each candidate with the as-of web tools (app/tools/asof.py: Wikipedia
     as it read then, SEC filings already accepted, Internet Archive captures, local prices up to that day)
     and gives P(the stock rises over the next 20 trading days).
The simulator then compares, on the same candidates:
  llm_web       top 10 by the LLM's probability
  screen_top10  top 10 by the screen alone (the control: what the LLM must beat)

Leak guard the tools can't provide: qwen3:8b was trained on web text through ~2024 and is told the ticker and
the date, so before its training cutoff it may remember what happened. Decisions are tagged `clean` (on or
after --clean-from, default 2025-01-01) or `memory_risk`; only the clean ones count as evidence.

Resumable and replayable: each decision is saved to results/llm_web_v2/<date>/<ticker>.json, LLM answers go to the
usual LLM cache, and every tool result to results/webcache/, so a re-run makes no new calls. Signals for the
simulator are written to results/llm_web_v2_signals.jsonl (feed to scripts/longrun.py --extra).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import pandas as pd

from app.sandbox import provenance as prov
from app.sandbox.gpu_lock import gpu_job
from app.sandbox.jail import AgentJail, JailError, JailLimits
from app.sandbox.longrun import SIGNED, Panel, cross_section, feature_frames
from app.sandbox.walkforward import OllamaLLM
from app.tools.gateway import ToolGateway
from app.tools.netguard import SafeFetcher

RESULTS = BACKEND / "results"
# v2 (2026-09-24): evidence-only relative question scored as unrounded log-odds. v1 (results/llm_web/) scored
# after the model's own conclusion and 43% of its answers saturated at P >= 0.9999, so most picks were tie-breaks.
OUT_DIR = RESULTS / "llm_web_v2"
SIGNALS = RESULTS / "llm_web_v2_signals.jsonl"
WEBCACHE = RESULTS / "webcache"
HORIZON = 20


def monthly_days(panel: Panel, start: date, end: date, every: int) -> list[pd.Timestamp]:
    """The last trading day of each month in [start, end], keeping every `every`-th month."""
    idx = panel.close.index
    s = pd.Series(idx, index=idx)
    last = s.groupby([idx.year, idx.month]).max()
    days = [d for d in last if start <= d.date() <= end]
    return days[::every]


def screen(panel: Panel, feats: dict[str, pd.DataFrame], d: pd.Timestamp, n: int) -> pd.Series:
    x = cross_section(panel, feats, d)
    comp = sum(x[k].mul(s).rank(pct=True) for k, s in SIGNED.values()) / len(SIGNED)
    return comp.sort_values(ascending=False).head(n)


def signal_p(rec: dict) -> float | None:
    """A rank score in (0, 1): the logistic of log-odds / 10. Dividing by 10 keeps values like 25 vs 30 apart
    instead of both becoming 1.0; it is monotone, so the ranking is exactly the log-odds ranking."""
    if "logodds" in rec and rec.get("logprob_mass", 0.0) > 0.05:
        return 1.0 / (1.0 + math.exp(-float(rec["logodds"]) / 10.0))
    if rec.get("logprob_mass", 0.0) > 0.05:
        return float(rec["p_up_logprob"])
    return rec.get("p_up")


def price_lookup(panel: Panel):
    close = panel.close

    def fn(ticker: str, as_of: datetime, n: int) -> list[tuple[str, float]]:
        if ticker not in close.columns:
            return []
        s = close[ticker].loc[: pd.Timestamp(as_of.date())].dropna().tail(n)
        return [(d.date().isoformat(), float(v)) for d, v in s.items()]
    return fn


async def decide(ticker: str, d: pd.Timestamp, llm: OllamaLLM, fetcher: SafeFetcher, base: ToolGateway,
                 lookup, rounds: int, brief: bool = False) -> dict:
    as_of = datetime(d.year, d.month, d.day, 20, 0, tzinfo=UTC)  # 16:00 New York in summer, 15:00 in winter
    gw = ToolGateway(mode="as_of", fetcher=fetcher, as_of=as_of, sec_user_agent=base.sec_user_agent,
                     price_lookup=lookup, tool_cache=WEBCACHE, max_result_chars=5000)
    t0 = time.monotonic()
    calls0 = llm.calls
    for attempt in range(2):
        try:
            async with AgentJail(llm, tools=gw, limits=JailLimits()) as jail:
                res = await jail.call({
                    "task": "research", "prompt": "as_of", "score": "logodds", "brief": brief,
                    "subject": {"ticker": ticker, "horizon_days": HORIZON, "as_of": as_of.isoformat()},
                    "tools": gw.specs_for_prompt(), "max_rounds": rounds, "max_calls_per_round": 4,
                    "num_ctx": llm.num_ctx, "num_predict": llm.num_predict})
            return {"ticker": ticker, "as_of": as_of.isoformat(), "horizon_days": HORIZON, **res,
                    "tool_log": gw.log, "llm_calls": llm.calls - calls0, "attempts": attempt + 1,
                    "elapsed_s": round(time.monotonic() - t0, 1)}
        except (JailError, OSError) as e:
            err = f"{type(e).__name__}: {e}"[:500]
    return {"ticker": ticker, "as_of": as_of.isoformat(), "p_up": None, "answered": False, "error": err,
            "tool_log": gw.log, "elapsed_s": round(time.monotonic() - t0, 1)}


async def run(args: argparse.Namespace) -> None:
    panel = Panel.load(pd.read_parquet(BACKEND / args.ohlcv), pd.read_csv(BACKEND / args.universe))
    feats = feature_frames(panel.close)
    days = monthly_days(panel, date.fromisoformat(args.date_from), date.fromisoformat(args.date_to), args.every)
    if args.newest_first:
        days = days[::-1]
    global OUT_DIR, SIGNALS
    OUT_DIR, SIGNALS = RESULTS / args.out, RESULTS / f"{args.out}_signals.jsonl"
    llm = OllamaLLM(args.model, base_url=args.base_url, concurrency=1, num_ctx=8192, num_predict=1200 if args.brief else 600, cache=True, require_gpu=True)
    base = ToolGateway.from_env("as_of", as_of=datetime(2000, 1, 1, tzinfo=UTC))
    if not base.sec_user_agent:
        raise SystemExit("set SEC_USER_AGENT in backend/.env (SEC requires a contact)")
    fetcher = SafeFetcher(base.fetcher.user_agent, timeout_s=40.0, total_timeout_s=80.0)
    await base.aclose()
    lookup = price_lookup(panel)
    clean_from = date.fromisoformat(args.clean_from)
    provenance = prov.collect(BACKEND.parent, BACKEND / args.ohlcv, args.model)
    done_signals = set()
    if SIGNALS.exists():
        for line in SIGNALS.read_text().splitlines():
            r = json.loads(line)
            done_signals.add((r["arm"], r["cutoff"], r["ticker"]))
    jobs = []
    for d in days:
        folder = OUT_DIR / d.date().isoformat()
        folder.mkdir(parents=True, exist_ok=True)
        label = "clean" if d.date() >= clean_from else "memory_risk"
        jobs += [(d, t, float(sc), label, folder / f"{t}.json") for t, sc in screen(panel, feats, d, args.candidates).items()]
    total, todo = len(jobs), sum(not j[4].exists() for j in jobs)
    print(f"{total} decisions, {total - todo} already done, {todo} to go, {args.workers} at a time", flush=True)
    done = 0
    t_start = time.monotonic()
    queue: asyncio.Queue[tuple[pd.Timestamp, str, float, str, Path]] = asyncio.Queue()
    for j in jobs:
        queue.put_nowait(j)

    def record_signals(rec: dict, d: pd.Timestamp, t: str, sc: float, label: str) -> None:
        with SIGNALS.open("a") as f:
            for arm, p in (("llm_web" if label == "clean" else "llm_web_memory_risk", signal_p(rec)),
                           ("screen_top10" if label == "clean" else "screen_top10_memory_risk", 0.45 + 0.1 * sc)):
                key = (arm, d.date().isoformat(), t)
                if p is not None and key not in done_signals:
                    f.write(json.dumps({"arm": arm, "cutoff": key[1], "ticker": t, "p": p}) + "\n")
                    done_signals.add(key)

    async def worker() -> None:
        # several decisions in flight: almost all of a decision's time is waiting on the network, so they overlap.
        # The GPU still answers one prompt at a time (OllamaLLM concurrency=1), so every answer is unchanged.
        nonlocal done
        while not queue.empty():
            d, t, sc, label, path = queue.get_nowait()
            if path.exists():
                rec = json.loads(path.read_text())
            else:
                rec = await decide(t, d, llm, fetcher, base, lookup, args.rounds, args.brief)
                rec |= {"window": label, "screen_score": sc, "model": args.model, "provenance": provenance}
                path.write_text(json.dumps(rec, indent=1, default=str) + "\n")
                done += 1
                rate = (time.monotonic() - t_start) / done
                print(f"[{done}/{todo}] {d.date()} {t:6s} p={rec.get('p_up')} logodds={rec.get('logodds')} "
                      f"{rec.get('elapsed_s')}s tools={len(rec.get('tool_log', []))} "
                      f"ok={sum(e['ok'] for e in rec.get('tool_log', []))} "
                      f"{rate:.1f}s/decision eta={(todo - done) * rate / 3600:.1f}h", flush=True)
            record_signals(rec, d, t, sc, label)

    try:
        await asyncio.gather(*(worker() for _ in range(max(1, args.workers))))
    finally:
        await fetcher.aclose()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="date_from", default="2025-01-01")
    ap.add_argument("--to", dest="date_to", default="2026-08-31")
    ap.add_argument("--every", type=int, default=1, help="keep every n-th month")
    ap.add_argument("--candidates", type=int, default=20)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--workers", type=int, default=4, help="decisions researched at the same time")
    ap.add_argument("--brief", action="store_true", help="also write a source-checked research brief (for scripts/decide.py)")
    ap.add_argument("--out", default="llm_web_v2", help="results sub-folder, e.g. analyst for the brief-writing run")
    ap.add_argument("--base-url", default=None, help="Ollama server, default http://127.0.0.1:11434")
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--clean-from", default="2025-01-01", help="first decision day after the model's training data")
    ap.add_argument("--newest-first", action="store_true")
    ap.add_argument("--universe", default="data/hist/universe_2010_2026_top100.csv")
    ap.add_argument("--ohlcv", default="data/hist/ohlcv_2010_2026_top100.parquet")
    args = ap.parse_args()
    with gpu_job("llm_web_backtest"):
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
