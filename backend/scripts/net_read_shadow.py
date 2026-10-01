"""Arms C2, D and E (docs/PLAN_60_V2.md): Bonsai's `net_read`, its call through the AI-build-out lens (`ai_read`, the
Aschenbrenner view the user asked for) and its bull/bear thesis (`bb_read`, with the quoted points of each side), on
live releases only: shadows with no money.

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
from llm_fields import (
    FIELDS_AI,
    FIELDS_BB,
    FIELDS_J,
    PROMPT_AI,
    PROMPT_BB,
    PROMPT_J,
    ask,
    parse,
    text_of,
    theses,
    verify,
)

from app.forward.ledger import Ledger, jsonl_records, open_append
from app.sandbox.gpu_lock import gpu_priority

PORT = 11440
SCORE = {"bullish": 1, "neutral": 0, "bearish": -1}
# lens -> (prompt, fields, the scored field, its file): C2 (net_read), arm D (the AI-build-out lens) and arm E (the
# bull/bear thesis), all live-only
LENSES: dict[str, tuple[str, dict[str, tuple[tuple[str, ...], str]], str, str]] = {
    "net_read": (PROMPT_J, FIELDS_J, "net_read", "net_read.jsonl"),
    "ai_lens": (PROMPT_AI, FIELDS_AI, "ai_read", "ai_lens.jsonl"),
    "bull_bear": (PROMPT_BB, FIELDS_BB, "bb_read", "bull_bear.jsonl")}


def lenses(d: Path) -> dict[str, tuple[str, dict[str, tuple[tuple[str, ...], str]], str, str]]:
    """The fixed lenses plus arm F's self-improving versions (scripts/self_improve.py), if any."""
    try:
        from self_improve import active_lenses
        return LENSES | active_lenses(d)
    except Exception as e:  # noqa: BLE001 - arm F's file must never stop the fixed lenses from labelling
        print(f"LEARN ALERT: self-improve versions unreadable ({type(e).__name__}: {e}); fixed lenses only"[:300])
        return dict(LENSES)


PASS = {"min_events": 150, "min_months": 3, "min_ic": 0.02}  # judged with learn_loop's blend-gain test as well


def user_text(acc: str) -> tuple[str, str] | None:
    text = text_of(acc)
    if text is None:
        return None
    return text[:6000] + "\n\n=== Research evidence (web, as of the release) ===\nNo research evidence was found.", text[:6000]


def todo(recs: list[dict[str, Any]], done: set[str]) -> list[dict[str, Any]]:
    return [r for r in recs if r.get("type") == "decision" and r["accession"] not in done]


async def label(llm: Any, r: dict[str, Any], lens: str = "net_read",
                spec: tuple[str, dict[str, tuple[tuple[str, ...], str]], str, str] | None = None) -> dict[str, Any]:
    prompt, fields, key, _ = spec or LENSES[lens]
    u = user_text(r["accession"])
    rec: dict[str, Any] = {"accession": r["accession"], "ticker": r["ticker"], "entry_deadline": r["entry_deadline"]}
    if u is None:
        return rec | {key: "neutral", "parsed": False, "note": "no release text"}
    user, source = u
    reply, overflow = await ask(llm, prompt, user)
    raw = parse(reply)
    v = verify(raw, source, fields)
    rec |= {key: v[key], "parsed": v["parsed"], "overflow": overflow, "fields": {f: v[f] for f in fields}}
    if key == "bb_read":
        rec |= theses(raw, source) | {"reason": str((raw or {}).get("reason", ""))[:300]}
    return rec


def scored(d: Path, lens: str = "net_read") -> pd.DataFrame:
    """On-time labels joined with matured outcomes: accession, month, net, fwd5."""
    _, _, key, fname = lenses(d)[lens]
    p = d / fname
    if not p.exists() or not (d / "ledger.jsonl").exists():
        return pd.DataFrame(columns=["accession", "month", "net", "fwd5"])
    lab = [x for x in jsonl_records(p) if key in x and "written_at" in x]
    lab = [x for x in lab if datetime.fromisoformat(x["written_at"]) < datetime.fromisoformat(x["entry_deadline"])]
    out = {r["accession"]: r for r in Ledger(d / "ledger.jsonl").records() if r.get("type") == "outcome"}
    rows = [{"accession": x["accession"], "month": out[x["accession"]]["entry"][:7], "net": SCORE[x[key]],
             "fwd5": out[x["accession"]]["fwd5"]} for x in lab
            if x["accession"] in out and out[x["accession"]].get("fwd5") is not None]
    return pd.DataFrame(rows, columns=["accession", "month", "net", "fwd5"])


def status(d: Path, lens: str = "net_read") -> dict[str, Any]:
    df = scored(d, lens)
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


def run(d: Path, use_gpu: bool) -> dict[str, int]:
    """Label every unlabelled decision whose entry deadline is still ahead, with each lens, in one GPU session."""
    ledger = d / "ledger.jsonl"
    if not ledger.exists():
        return {}
    recs_all = Ledger(ledger).records()
    now = datetime.now(UTC)
    todo_by: dict[str, list[dict[str, Any]]] = {}
    specs = lenses(d)
    for lens, (_, _, _, fname) in specs.items():
        p = d / fname
        done = {x["accession"] for x in jsonl_records(p) if "accession" in x}
        rows = [r for r in todo(recs_all, done) if datetime.fromisoformat(r["entry_deadline"]) > now]
        if rows:
            todo_by[lens] = rows
    if not todo_by or not use_gpu:
        return {}
    from app.sandbox.walkforward import OllamaLLM
    with gpu_priority("net_read_shadow"):
        if not wait_gpu_free(900):
            print("LEARN ALERT: net_read shadow: the GPU stayed busy; releases left unlabelled")
            return {}
        srv = Ollama(PORT, str(Path.home() / ".ollama" / "models"), 3, d / "ollama_net_read.log")
        try:
            llm = OllamaLLM("bonsai-27b:latest", base_url=f"http://127.0.0.1:{PORT}", concurrency=3, num_ctx=8192,
                            num_predict=1200, cache=False, require_gpu=True)

            made = dict.fromkeys(todo_by, 0)

            async def one(lens: str, r: dict[str, Any]) -> None:
                # each label is written the moment it is made, with its own time: on a morning with many releases
                # the last label can come after the open, and one time stamp at the end would make them all late
                rec = await label(llm, r, lens, specs[lens])
                with open_append(d / specs[lens][3]) as f:
                    f.write(json.dumps(rec | {"written_at": datetime.now(UTC).isoformat(timespec="seconds")}) + "\n")
                made[lens] += 1

            async def go() -> list[BaseException | None]:
                # release by release (earliest open first), so a release has all its labels as early as possible;
                # one label that fails must not lose the others: these labels cannot be made afterwards
                jobs = sorted(((r["entry_deadline"], i, lens, r) for lens, rows in todo_by.items()
                               for i, r in enumerate(rows)), key=lambda x: x[:2])
                try:
                    return list(await asyncio.gather(*(one(lens, r) for _, _, lens, r in jobs),
                                                     return_exceptions=True))
                finally:
                    await llm.unload()
            failed = [x for x in asyncio.run(go()) if isinstance(x, BaseException)]
        finally:
            srv.stop()
    if failed:
        print(f"LEARN ALERT: net_read shadow: {len(failed)} label(s) failed and are tried again next run "
              f"({type(failed[0]).__name__}: {failed[0]})"[:300])
    return made


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
            print(f"live-only shadows labelled: {json.dumps(n)}")
        print(json.dumps({lens: status(d, lens) for lens in lenses(d)}))
    except Exception as e:  # noqa: BLE001 - a shadow: never fail the events job
        print(f"LEARN ALERT: net_read shadow failed: {type(e).__name__}: {e}"[:300])


if __name__ == "__main__":
    main()
