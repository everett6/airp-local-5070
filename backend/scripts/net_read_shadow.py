"""Arm C2 (docs/PLAN_60_V2.md "Arm C2"): Bonsai's `net_read` on live releases only, a shadow with no money.

    python scripts/net_read_shadow.py --dir results/forward/events      # label new decisions, then print the score
    python scripts/net_read_shadow.py --dir results/forward/events --status

For every decision in the event ledger without a label, Bonsai reads the release with arm C's PROMPT_J (same prompt,
same quote check; the live runner has no Jan research, so the evidence part says none was found). The label goes to
<dir>/net_read.jsonl with the time it was written; a label written at or after the entry deadline is kept but never
scored. Score: bullish +1, neutral 0, bearish -1. The ledger itself is never touched. Runs after forward_events.py in
the events job; problems print as "LEARN ALERT: ..." lines (autorun turns them into alerts) and never fail the job.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np
import pandas as pd
from forward_events import Ollama, wait_gpu_free
from llm_fields import FIELDS_J, PROMPT_J, ask, parse, text_of, verify

from app.forward.ledger import Ledger
from app.sandbox.gpu_lock import gpu_priority

PORT = 11440
SCORE = {"bullish": 1, "neutral": 0, "bearish": -1}
PASS = {"min_events": 150, "min_months": 3, "min_ic": 0.02}  # judged with learn_loop's blend-gain test as well


def user_text(acc: str) -> tuple[str, str] | None:
    text = text_of(acc)
    if text is None:
        return None
    return text[:6000] + "\n\n=== Research evidence (web, as of the release) ===\nNo research evidence was found.", text[:6000]


def todo(recs: list[dict[str, Any]], done: set[str]) -> list[dict[str, Any]]:
    return [r for r in recs if r.get("type") == "decision" and r["accession"] not in done]


async def label(llm: Any, r: dict[str, Any]) -> dict[str, Any]:
    u = user_text(r["accession"])
    rec: dict[str, Any] = {"accession": r["accession"], "ticker": r["ticker"], "entry_deadline": r["entry_deadline"]}
    if u is None:
        return rec | {"net_read": "neutral", "parsed": False, "note": "no release text"}
    user, source = u
    reply, overflow = await ask(llm, PROMPT_J, user)
    v = verify(parse(reply), source, FIELDS_J)
    return rec | {"net_read": v["net_read"], "parsed": v["parsed"], "overflow": overflow,
                  "fields": {f: v[f] for f in FIELDS_J}}


def scored(d: Path) -> pd.DataFrame:
    """On-time labels joined with matured outcomes: accession, month, net, fwd5."""
    p = d / "net_read.jsonl"
    if not p.exists() or not (d / "ledger.jsonl").exists():
        return pd.DataFrame(columns=["accession", "month", "net", "fwd5"])
    lab = [json.loads(x) for x in p.read_text().splitlines()]
    lab = [x for x in lab if datetime.fromisoformat(x["written_at"]) < datetime.fromisoformat(x["entry_deadline"])]
    out = {r["accession"]: r for r in Ledger(d / "ledger.jsonl").records() if r.get("type") == "outcome"}
    rows = [{"accession": x["accession"], "month": out[x["accession"]]["entry"][:7], "net": SCORE[x["net_read"]],
             "fwd5": out[x["accession"]]["fwd5"]} for x in lab
            if x["accession"] in out and out[x["accession"]].get("fwd5") is not None]
    return pd.DataFrame(rows, columns=["accession", "month", "net", "fwd5"])


def status(d: Path) -> dict[str, Any]:
    df = scored(d)
    raw = [g["net"].rank().corr(g["fwd5"].rank()) for _, g in df.groupby("month")
           if len(g) >= 10 and g["net"].nunique() > 1]
    ics = np.array([x for x in raw if not np.isnan(x)])
    lo80 = None
    if len(ics) > 1:
        b = ics[np.random.default_rng(0).integers(0, len(ics), (5000, len(ics)))].mean(1)
        lo80 = round(float(np.percentile(b, 20)), 4)
    ready = len(df) >= PASS["min_events"] and len(ics) >= PASS["min_months"]
    return {"events": len(df), "months": len(ics), "mean_ic": round(float(ics.mean()), 4) if len(ics) else None,
            "ic_lo80": lo80, "ready_to_judge": ready,
            "counts": df["net"].map({1: "bullish", 0: "neutral", -1: "bearish"}).value_counts().to_dict()}


def run(d: Path, use_gpu: bool) -> int:
    ledger = d / "ledger.jsonl"
    if not ledger.exists():
        return 0
    p = d / "net_read.jsonl"
    done = {json.loads(x)["accession"] for x in p.read_text().splitlines()} if p.exists() else set()
    rows = todo(Ledger(ledger).records(), done)
    now = datetime.now(UTC)
    rows = [r for r in rows if datetime.fromisoformat(r["entry_deadline"]) > now]  # too late to be scored: skip
    if not rows or not use_gpu:
        return 0
    from app.sandbox.walkforward import OllamaLLM
    with gpu_priority("net_read_shadow"):
        if not wait_gpu_free(900):
            print("LEARN ALERT: net_read shadow: the GPU stayed busy; releases left unlabelled")
            return 0
        srv = Ollama(PORT, str(Path.home() / ".ollama" / "models"), 3, d / "ollama_net_read.log")
        try:
            llm = OllamaLLM("bonsai-27b:latest", base_url=f"http://127.0.0.1:{PORT}", concurrency=3, num_ctx=8192,
                            num_predict=1000, cache=False, require_gpu=True)

            async def go() -> list[dict[str, Any]]:
                try:
                    return list(await asyncio.gather(*(label(llm, r) for r in rows)))
                finally:
                    await llm.unload()
            recs = asyncio.run(go())
        finally:
            srv.stop()
    at = datetime.now(UTC).isoformat(timespec="seconds")
    with p.open("a") as f:
        for r in recs:
            f.write(json.dumps(r | {"written_at": at}) + "\n")
    return len(recs)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="results/forward/events")
    ap.add_argument("--no-gpu", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    d = BACKEND / a.dir
    try:
        if not a.status:
            n = run(d, not a.no_gpu)
            print(f"net_read shadow: {n} release(s) labelled")
        print(json.dumps(status(d)))
    except Exception as e:  # noqa: BLE001 - a shadow: never fail the events job
        print(f"LEARN ALERT: net_read shadow failed: {type(e).__name__}: {e}"[:300])


if __name__ == "__main__":
    main()
