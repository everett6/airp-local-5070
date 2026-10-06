"""Does letting Bonsai look things up reduce its "3 / unclear" ratings? (engineering check, 4 Oct 2026)

    python scripts/judge_lookup_check.py [--n 20]

The same deep-research cards (latest decided record per company, a seeded sample) are judged twice with the same
prompt: once as they are, once after Bonsai's own lookups (app/sandbox/judge_lookup.py). It counts directional
ratings that survive the quote check in each arm. This measures how often Bonsai can make a supported call, not
whether the calls are right: no prices, no orders, no register(); a profitability claim needs a registered test.
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import random
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from forward_events import Ollama, wait_gpu_free
from jan_forward import MODELS
from live_research_test import FreeLiveGateway, judge_prompt
from llm_fields import ask, parse, verify

from app.forward.ledger import write_atomic
from app.sandbox.gpu_lock import gpu_job, gpu_priority
from app.sandbox.judge_lookup import extended_card, lookups
from app.sandbox.walkforward import OllamaLLM

FWD = BACKEND / "results" / "forward"
HORIZONS = ("day", "short", "medium", "long")
FIELDS: dict[str, tuple[tuple[str, ...], str]] = {h: (("1", "2", "3", "4", "5"), "3") for h in HORIZONS}


def sample(n: int, seed: int = 4) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for f in (FWD / "deep_research").glob("*/*.json"):
        if f.name == "summary.json":
            continue
        r = json.loads(f.read_text())
        if r.get("status") == "decided" and r.get("card") and r["decided_at"] > best.get(f.stem, {}).get("decided_at", ""):
            best[f.stem] = {"ticker": f.stem, "card": r["card"], "decided_at": r["decided_at"], "old": r["ratings"],
                            "path": str(f)}
    rows = sorted(best.values(), key=lambda r: r["ticker"])
    return random.Random(seed).sample(rows, min(n, len(rows)))


async def judge(llm: OllamaLLM, card: str) -> dict[str, Any]:
    t = time.monotonic()
    reply, overflow = await ask(llm, judge_prompt(True), card)
    raw = parse(reply)
    labels = verify(raw, card, FIELDS) if raw is not None and not overflow else {"parsed": False}
    return {"labels": {h: int(labels.get(h, 3)) for h in HORIZONS}, "claimed": labels.get("claimed", 0),
            "verified": labels.get("verified", 0), "parsed": labels.get("parsed", False), "reply": reply,
            "s": round(time.monotonic() - t, 1)}


async def check(rows: list[dict[str, Any]], out: Path) -> dict[str, Any]:
    llm = OllamaLLM("bonsai-27b:latest", base_url="http://127.0.0.1:11447", concurrency=1, num_ctx=8192,
                    num_predict=1200, cache=False, require_gpu=True)
    srv = Ollama(11447, MODELS, 1, out.with_suffix(".ollama.log"))
    results = []
    try:
        for r in rows:
            plain = await judge(llm, r["card"])
            gw = FreeLiveGateway.from_env("live", max_result_chars=6000, timeout_cap_s=15, tool_cache=None)
            t = time.monotonic()
            try:
                async with asyncio.timeout(240):
                    look = await lookups(llm, gw, r["card"], gw.specs_for_prompt())
            except TimeoutError:
                look = {"steps": [], "evidence": "", "error": "timed out"}
            finally:
                await gw.aclose()
            look_s = round(time.monotonic() - t, 1)
            card = extended_card(r["card"], look["evidence"])
            looked = await judge(llm, card)
            results.append({**{k: r[k] for k in ("ticker", "decided_at", "old", "path")}, "plain": plain,
                            "lookup": {**looked, "lookup_s": look_s, "steps": look["steps"], "tool_log": gw.log,
                                       "card_chars": len(card)}})
            write_atomic(out, json.dumps({"rows": results}, indent=1) + "\n")
            print(f"{r['ticker']}: plain {plain['labels']}  with lookups {looked['labels']}  "
                  f"({len(look['steps'])} rounds, {look_s}s)", flush=True)
    finally:
        try:
            await llm.unload()
        finally:
            srv.stop()

    def directional(arm: str) -> int:
        return sum(v != 3 for x in results for v in x[arm]["labels"].values())

    summary = {"companies": len(results), "ratings": len(results) * len(HORIZONS),
               "directional_plain": directional("plain"), "directional_lookup": directional("lookup"),
               "all_unclear_plain": sum(all(v == 3 for v in x["plain"]["labels"].values()) for x in results),
               "all_unclear_lookup": sum(all(v == 3 for v in x["lookup"]["labels"].values()) for x in results),
               "claims_rejected_plain": sum(x["plain"]["claimed"] - x["plain"]["verified"] for x in results),
               "claims_rejected_lookup": sum(x["lookup"]["claimed"] - x["lookup"]["verified"] for x in results),
               "median_lookup_s": sorted(x["lookup"]["lookup_s"] for x in results)[len(results) // 2] if results else None}
    write_atomic(out, json.dumps({"summary": summary, "rows": results}, indent=1) + "\n")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=20)
    a = ap.parse_args()
    rows = sample(a.n)
    out = FWD / "judge_lookup_check" / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + ".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    with (FWD / "autorun.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("A scheduled or app job is running; try later") from None
        from desktop_run import preflight
        reason = preflight("live_research_test", FWD, datetime.now(UTC))
        if reason:
            raise SystemExit(reason)
        with gpu_priority("judge lookup check"):
            if not wait_gpu_free(300):
                raise SystemExit("GPU busy")
            with gpu_job("judge lookup check"):
                print(json.dumps(asyncio.run(check(rows, out)), indent=1))


if __name__ == "__main__":
    main()
