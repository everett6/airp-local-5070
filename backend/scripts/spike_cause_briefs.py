"""Spike check, Mon 28 night: Jan gathers as-of evidence for every historical trigger (results/spike_triggers.csv),
then Bonsai labels the cause (docs/STRATEGY_RESEARCH.md, "Spike check").

    python scripts/spike_cause_briefs.py --phase gather   # Jan on vLLM (scripts/vllm_serve.sh)
    python scripts/spike_cause_briefs.py --phase label    # Bonsai on Ollama

Evidence is as of the trigger day's close (21:00 UTC); the response it informs is traded at the next open. Labels:
earnings, company_news, sector, macro, unexplained. Code, not the model, has the last word: a label other than
unexplained must cite a source tag that a tool actually returned, else it becomes unexplained; "earnings" also
needs an 8-K earnings release for the ticker accepted on the trigger day or the day before. Results:
results/spike_briefs/<day>_<asset>.json and results/spike_causes.csv. Resumable.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import pandas as pd
from research_events import Router, lookup_from, research

from app.sandbox.agent_worker import _json_object, source_tags
from app.sandbox.events import Prices
from app.sandbox.vllm_client import VLLMChat
from app.sandbox.walkforward import OllamaLLM
from app.tools.gateway import ToolGateway
from app.tools.netguard import SafeFetcher

OUT = BACKEND / "results" / "spike_briefs"
LABELS = ("earnings", "company_news", "sector", "macro", "unexplained")
LABEL_SYSTEM = """You are a risk analyst. An asset in the portfolio moved far more than usual today. Using ONLY the
evidence in the user message, say what most likely caused the move.

Labels:
- earnings: the company's own earnings release or guidance
- company_news: other news about this company (deal, lawsuit, product, management, rating change)
- sector: news that moved its industry or peers
- macro: a market-wide move (rates, inflation, jobs data, policy, broad sell-off or rally)
- unexplained: the evidence does not show a cause

Reply with ONLY one JSON object on one line:
{"label": "<one label>", "reason": "<one sentence>", "source": "<the tag of the source that shows the cause, e.g. S2, or empty>"}
Pick unexplained unless a source in the evidence shows the cause."""


def triggers() -> pd.DataFrame:
    df = pd.read_csv(BACKEND / "results" / "spike_triggers.csv")
    df["key"] = df["day"].astype(str) + "_" + df["asset"].astype(str)
    return df


def earnings_days() -> dict[str, set[str]]:
    ev = pd.read_csv(BACKEND / "data" / "events" / "events_sp500_2024_2026.csv")
    out: dict[str, set[str]] = {}
    for t, a in zip(ev["ticker"], ev["accepted_utc"], strict=True):
        out.setdefault(str(t).replace(".", "-"), set()).add(str(a)[:10])
    return out


async def gather(args: argparse.Namespace) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    todo = [r for r in triggers().itertuples() if not (OUT / f"{r.key}.json").exists()]
    print(f"{len(todo)} triggers to gather", flush=True)
    llm = VLLMChat(args.vllm_model, base_url=args.vllm_url, concurrency=2 * args.workers)
    base = ToolGateway.from_env("as_of", as_of=datetime(2000, 1, 1, tzinfo=UTC))
    if not base.sec_user_agent:
        raise SystemExit("set SEC_USER_AGENT in backend/.env")
    fetcher = SafeFetcher(base.fetcher.user_agent, timeout_s=40.0, total_timeout_s=80.0)
    ua = base.sec_user_agent
    llm.native_tools = base.specs_native()
    await base.aclose()
    lookup = lookup_from(Prices.from_long(pd.read_parquet(BACKEND / "data/events/ohlcv_2023-01-01_2026-09-25.parquet")))
    sem, n, t0 = asyncio.Semaphore(args.workers), 0, time.monotonic()

    async def one(r) -> None:
        nonlocal n
        async with sem:
            row = SimpleNamespace(accession=f"spike-{r.key}", ticker=r.asset, accepted_utc=f"{r.day}T21:00:00")
            rec = await research(row, Router(llm, None), fetcher, ua, lookup, args.rounds, None, 15.0, brief=False)
            rec.update({"day": r.day, "asset": r.asset, "ret_pct": r.ret_pct, "z": r.z, "fwd5_pct": r.fwd5_pct})
            (OUT / f"{r.key}.json").write_text(json.dumps(rec, indent=1, default=str) + "\n")
            n += 1
            print(f"[{n}/{len(todo)}] {r.key} tools={len(rec.get('tool_log', []))} {rec['elapsed_s']}s "
                  f"eta={(len(todo) - n) * (time.monotonic() - t0) / n / 60:.0f}min", flush=True)
    try:
        await asyncio.gather(*(one(r) for r in todo))
    finally:
        await fetcher.aclose()
        await llm.unload()


def check(obj: dict | None, tags: dict[str, str], earn: bool) -> dict:
    obj = obj or {}
    label = str(obj.get("label", "")).strip().lower()
    src = str(obj.get("source", "")).strip().strip("[]").upper()
    out = {"label_model": label, "reason": str(obj.get("reason", ""))[:400], "source": tags.get(src, "")}
    if label not in LABELS or (label != "unexplained" and src not in tags) or (label == "earnings" and not earn):
        label = "unexplained"
    out["label"] = label
    return out


async def label(args: argparse.Namespace) -> None:
    writer = OllamaLLM(args.brief_model, base_url=args.brief_base_url, concurrency=3, num_ctx=8192, num_predict=300,
                       require_gpu=True)
    earn = earnings_days()
    todo = [p for p in sorted(OUT.glob("*.json")) if "cause" not in json.loads(p.read_text())]
    print(f"{len(todo)} triggers to label", flush=True)
    sem = asyncio.Semaphore(3)

    async def one(p: Path) -> None:
        async with sem:
            rec = json.loads(p.read_text())
            ev = rec.get("evidence") or ""
            tags = source_tags(ev)
            listing = "\n\nSources:\n" + "\n".join(f"[{t}] {u}" for t, u in tags.items()) if tags else ""
            user = (f"Asset: {rec['asset']}. Day: {rec['day']} (evidence as of that day's close). "
                    f"Move today: {rec['ret_pct']}% ({rec['z']} times its usual daily volatility).\n\n"
                    + (ev or "No evidence was found.") + listing)
            d = datetime.fromisoformat(str(rec["day"]))
            near = {str(rec["day"]), (d - timedelta(days=1)).date().isoformat()}
            is_earn = bool(earn.get(str(rec["asset"]), set()) & near)
            try:
                reply = await writer(LABEL_SYSTEM, user)
            except Exception as e:  # noqa: BLE001 - one failure must not stop the run; a re-run retries it
                print(f"  label failed {p.stem}: {e}"[:200], flush=True)
                return
            rec["cause"] = check(_json_object(reply), tags, is_earn) | {"earnings_release_near": is_earn}
            p.write_text(json.dumps(rec, indent=1, default=str) + "\n")
    try:
        await asyncio.gather(*(one(p) for p in todo))
    finally:
        await writer.unload()
    rows = [json.loads(p.read_text()) for p in sorted(OUT.glob("*.json"))]
    df = pd.DataFrame([{k: r.get(k) for k in ("day", "asset", "ret_pct", "z", "fwd5_pct")}
                       | {k: (r.get("cause") or {}).get(k) for k in ("label", "label_model", "earnings_release_near",
                                                                      "reason", "source")} for r in rows])
    df.to_csv(BACKEND / "results" / "spike_causes.csv", index=False)
    print(df["label"].value_counts(dropna=False).to_string())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phase", choices=("gather", "label"), required=True)
    ap.add_argument("--vllm-url", default="http://127.0.0.1:8000")
    ap.add_argument("--vllm-model", default="jan-v1-4b")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--brief-model", default="bonsai-27b:latest")
    ap.add_argument("--brief-base-url", default="http://127.0.0.1:11435")
    args = ap.parse_args()
    asyncio.run(gather(args) if args.phase == "gather" else label(args))


if __name__ == "__main__":
    main()
