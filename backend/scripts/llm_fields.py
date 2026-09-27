"""LLM extracts, code scores (docs/PLAN_60_V2.md, "LLM extracts, code scores"; spec fixed before any extraction).

    python scripts/llm_fields.py dev                  # step 1: the 4 prompts on 100 dev releases, quality metrics only
    python scripts/llm_fields.py extract --prompt P3  # step 2: the frozen prompt on the 2024 and 2025-26 samples
    python scripts/llm_fields.py test                 # step 3: the one pre-registered return test

Bonsai reads each earnings press release and labels 7 things code cannot compute from the numbers, each with an
exact quote. Code keeps a non-default label only if its quote is word for word in the release. The prompt is chosen on
extraction quality (parse rate, verified quotes, agreement with keyword labels), never on stock returns.
Needs Bonsai on Ollama at :11435 for dev/extract (a manual `ollama serve`, not a service).
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np
import pandas as pd

from app.sandbox.events import _norm

TEXT = BACKEND / "data" / "events" / "text"
OUT = BACKEND / "results" / "events"
MAX_CHARS = 10000
FIELDS: dict[str, tuple[tuple[str, ...], str]] = {  # labels, default
    "one_off": (("charge", "gain", "none"), "none"),
    "demand": (("strengthening", "stable", "weakening", "not_stated"), "not_stated"),
    "margin": (("expanded", "stable", "contracted", "not_stated"), "not_stated"),
    "capital_return": (("increased", "cut", "none"), "none"),
    "leadership": (("change", "none"), "none"),
    "risk_flag": (("yes", "no"), "no"),
    "segment_weakness": (("yes", "no"), "no"),
}

_SCHEMA = "{" + ", ".join(f'"{k}": {{"label": "{"|".join(v[0])}", "quote": "..."}}' for k, v in FIELDS.items()) + "}"
_DEFS = """Definitions:
- one_off: a charge that lowered this quarter's GAAP results (impairment, restructuring, litigation, write-down) or a
  one-time gain (sale of a business or asset); none if the release names neither.
- demand: what management says about orders, backlog, bookings, pipeline, customer traffic or demand going forward;
  not_stated if it says nothing about it.
- margin: gross or operating margin this quarter vs the same quarter a year earlier, as the release states it;
  not_stated if the release gives no comparison.
- capital_return: increased = a new or larger share buyback authorization or a dividend increase announced in this
  release; cut = a dividend cut or suspension or a halted buyback; none otherwise (paying the usual dividend is none).
- leadership: change = the CEO or CFO is leaving, retiring or newly named in this release; none otherwise.
- risk_flag: yes = restatement, material weakness, going-concern doubt, delisting notice or a government
  investigation; no otherwise.
- segment_weakness: yes = management names a business segment or region whose sales or profit declined; no otherwise.
"""
_RULES = """Rules: use ONLY the release. For every label other than none / not_stated / no, copy the sentence that shows
it word for word as "quote" (at most 30 words); otherwise quote "". Never guess.
Reply with ONLY one JSON object on one line."""
_EX = """Examples of single fields:
"capital_return": {"quote": "The Board authorized a new $5 billion share repurchase program.", "label": "increased"}
"demand": {"quote": "", "label": "not_stated"}
"""
_SCHEMA_Q = "{" + ", ".join(f'"{k}": {{"quote": "...", "label": "{"|".join(v[0])}"}}' for k, v in FIELDS.items()) + "}"
_HEAD = "You read one quarterly earnings press release and label it for a portfolio manager.\n"
PROMPTS = {
    "P1": _HEAD + _RULES + "\nJSON: " + _SCHEMA,
    "P2": _HEAD + _DEFS + _RULES + "\nJSON: " + _SCHEMA,
    "P3": _HEAD + _DEFS + _RULES + "\nWrite each field's quote BEFORE its label.\nJSON: " + _SCHEMA_Q,
    "P4": _HEAD + _DEFS + _RULES + "\nWrite each field's quote BEFORE its label.\n" + _EX + "JSON: " + _SCHEMA_Q,
}

SILVER = {  # code-only keyword labels, the quality yardstick: does the model flag a field when the words are there?
    "one_off": r"impairment|restructuring (charge|cost|expense)|goodwill write|litigation (charge|settlement)|write-?down",
    "capital_return": r"((repurchase|buyback)[^.]{0,80}(authoriz|new|additional|increase))|((increas|rais)\w* (its |the |our )?(quarterly )?(cash )?dividend)|((suspend|reduc|cut)\w* (its |the |our )?(quarterly )?dividend)",
    "leadership": r"(chief executive officer|chief financial officer|\bceo\b|\bcfo\b)[^.]{0,60}(retire|resign|step(s|ping)? down|appoint|named|succeed|depart|transition)",
    "risk_flag": r"material weakness|restate|going concern|subpoena|investigation by the|delisting|wells notice",
}


def text_of(acc: str) -> str | None:
    p = TEXT / f"{acc}.txt.gz"
    return gzip.decompress(p.read_bytes()).decode()[:MAX_CHARS] if p.exists() else None


def parse(reply: str) -> dict[str, Any] | None:
    try:
        o = json.loads(reply[reply.index("{"): reply.rindex("}") + 1])
    except ValueError:
        return None
    return o if isinstance(o, dict) else None


def verify(raw: dict[str, Any] | None, text: str) -> dict[str, Any]:
    """Code has the last word: a non-default label needs a quote found word for word in the release."""
    body = _norm(text)
    out: dict[str, Any] = {"parsed": raw is not None, "claimed": 0, "verified": 0}
    for f, (labels, default) in FIELDS.items():
        v = (raw or {}).get(f)
        label = str(v.get("label", "")).strip().lower() if isinstance(v, dict) else ""
        quote = str(v.get("quote", "")).strip() if isinstance(v, dict) else ""
        if label not in labels:
            label = default
        if label != default:
            out["claimed"] += 1
            if len(quote) >= 12 and _norm(quote).strip('"') in body:
                out["verified"] += 1
            else:
                label = default
        out[f] = label
    return out


def silver(text: str) -> dict[str, bool]:
    t = _norm(text)
    return {f: bool(re.search(rx, t)) for f, rx in SILVER.items()}


async def run_prompt(llm: Any, system: str, rows: list[Any]) -> list[dict[str, Any]]:
    async def one(r: Any) -> dict[str, Any] | None:
        text = text_of(r.accession)
        if text is None:
            return None
        t0 = time.monotonic()
        reply = await llm(system, text)
        rec = {"accession": r.accession, "s": time.monotonic() - t0, **verify(parse(reply), text)}
        rec["silver"] = silver(text)
        return rec
    got = await asyncio.gather(*(one(r) for r in rows))
    return [g for g in got if g is not None]


def quality(recs: list[dict[str, Any]], wall: float) -> dict[str, float]:
    claimed = sum(r["claimed"] for r in recs)
    agree = [(r[f] != FIELDS[f][1]) == r["silver"][f] for r in recs for f in SILVER]
    q = {"n": len(recs), "parse_rate": float(np.mean([r["parsed"] for r in recs])),
         "verified_share": sum(r["verified"] for r in recs) / claimed if claimed else 0.0,
         "silver_agreement": float(np.mean(agree)), "s_per_release": wall / max(1, len(recs)),
         "non_default_per_release": sum(r["verified"] for r in recs) / max(1, len(recs))}
    q["score"] = q["parse_rate"] * q["verified_share"] * q["silver_agreement"]
    return {k: round(v, 4) for k, v in q.items()}


def llm_client() -> Any:
    from app.sandbox.walkforward import OllamaLLM
    return OllamaLLM("bonsai-27b:latest", base_url="http://127.0.0.1:11435", concurrency=3, num_ctx=8192,
                     num_predict=700, cache=True, require_gpu=True)


async def dev() -> None:
    ev = pd.read_csv(BACKEND / "data/events/events_sp500_2024.csv")
    rows = list(ev.sample(100, random_state=1).itertuples())
    llm = llm_client()
    res: dict[str, Any] = {}
    try:
        for name, system in PROMPTS.items():
            t0 = time.monotonic()
            recs = await run_prompt(llm, system, rows)
            res[name] = quality(recs, time.monotonic() - t0)
            print(name, res[name], flush=True)
    finally:
        await llm.unload()
    best = max(res, key=lambda k: res[k]["score"])
    close = [k for k in res if res[best]["score"] - res[k]["score"] <= 0.02]
    res["winner"] = min(close, key=lambda k: res[k]["s_per_release"])
    (OUT / "llm_fields_dev.json").write_text(json.dumps(res, indent=1) + "\n")
    print("winner (score, then speed within 0.02):", res["winner"])


async def extract(prompt: str) -> None:
    llm = llm_client()
    try:
        for tag, events, feats in (("2024", "data/events/events_sp500_2024.csv", "features_sp500_2024_secchk.csv"),
                                   ("2025", "data/events/events_sp500_2025.csv", "features_sp500_2025_secchk.csv")):
            out = OUT / f"llm_fields_{tag}.jsonl"
            done = {json.loads(x)["accession"] for x in out.read_text().splitlines()} if out.exists() else set()
            keep = set(pd.read_csv(OUT / feats)["accession"])
            rows = [r for r in pd.read_csv(BACKEND / events).itertuples() if r.accession in keep and r.accession not in done]
            print(f"{tag}: {len(rows)} releases to label with {prompt}", flush=True)
            t0 = time.monotonic()
            for i in range(0, len(rows), 60):
                recs = await run_prompt(llm, PROMPTS[prompt], rows[i:i + 60])
                with out.open("a") as fh:
                    for r in recs:
                        fh.write(json.dumps({**r, "prompt": prompt}) + "\n")
                n = i + len(rows[i:i + 60])
                print(f"  {n}/{len(rows)} eta={(len(rows) - n) * (time.monotonic() - t0) / n / 60:.0f}min", flush=True)
    finally:
        await llm.unload()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("dev", "extract", "test"))
    ap.add_argument("--prompt", default="")
    args = ap.parse_args()
    if args.cmd == "dev":
        asyncio.run(dev())
    elif args.cmd == "extract":
        if args.prompt not in PROMPTS:
            raise SystemExit("--prompt must be the frozen winner from `dev`")
        asyncio.run(extract(args.prompt))
    else:
        from llm_fields_test import main as test_main
        test_main()


if __name__ == "__main__":
    main()
