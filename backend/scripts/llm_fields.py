"""LLM extracts, code scores (docs/PLAN_60_V2.md, "LLM extracts, code scores"; spec fixed before any extraction).

    python scripts/llm_fields.py dev                  # step 1: the 4 prompts on 100 dev releases, quality metrics only
    python scripts/llm_fields.py extract --prompt P3  # step 2: the frozen prompt on the 2024 and 2025-26 samples
    python scripts/llm_fields.py test                 # step 3: the one pre-registered return test
    python scripts/llm_fields.py extract-research     # arm B: release + Jan's as-of research evidence
    python scripts/llm_fields.py test-research        # arm B's pre-registered test (research beyond arm A)
    python scripts/llm_fields.py dev-judgement        # arm C quality gate (100 dev releases; exit 3 = gate failed)
    python scripts/llm_fields.py extract-judgement    # arm C: arm B + four judgement fields and a reason
    python scripts/llm_fields.py test-judgement       # arm C's pre-registered test (judgement beyond arm B)

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

import httpx
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

FIELDS_R = FIELDS | {"vs_prior_guidance": (("beat", "met", "missed", "not_stated"), "not_stated")}
_SCHEMA_R = "{" + ", ".join(f'"{k}": {{"label": "{"|".join(v[0])}", "quote": "..."}}' for k, v in FIELDS_R.items()) + "}"
_DEF_R = """- vs_prior_guidance: this quarter's results vs the guidance range the company gave in its PREVIOUS earnings
  release (in the research evidence): beat = above the range, met = inside it, missed = below it; not_stated if the
  evidence has no earlier guidance for this quarter. Quote the earlier guidance.
"""
_RULES_R = _RULES.replace("use ONLY the release", "use ONLY the release and the research evidence").replace(
    "word for word", "word for word from the release or the evidence")
# arm B: P2 (frozen) plus the research evidence block and the one research-only field; nothing else changed
PROMPT_R = _HEAD + _DEFS + _DEF_R + _RULES_R + "\nJSON: " + _SCHEMA_R
EVIDENCE = BACKEND / "results"

# arm C (docs/PLAN_60_V2.md "Arm C"): arm B's prompt plus four JUDGEMENT fields and a short reason written first.
# Arm B's prompt and fields above are unchanged; this only adds.
FIELDS_J = FIELDS_R | {
    "earnings_quality": (("clean", "flattered", "not_clear"), "not_clear"),
    "outlook_tone": (("confident", "cautious", "not_stated"), "not_stated"),
    "net_read": (("bullish", "neutral", "bearish"), "neutral"),
    "conviction": (("high", "low"), "low"),
}
_DEF_J = """- earnings_quality: flattered = the headline growth or beat leans on one-offs, a lower tax rate, a smaller share
  count or adjustments that leave out recurring costs; clean = it comes from the core business (revenue and operating
  profit); not_clear otherwise. Quote the sentence that shows it.
- outlook_tone: how management talks about the coming quarters: confident = raised or firmly reaffirmed outlook with
  upbeat specifics; cautious = headwinds, uncertainty, softer trends or a lowered outlook; not_stated if nothing.
- net_read: YOUR judgement as a skeptical analyst, weighing the good against the bad (and the results against the
  earlier guidance, if the evidence has it): does this release make the next few weeks better (bullish) or worse
  (bearish) for the stock than a typical earnings release? neutral if it is mixed or ordinary. Quote the ONE sentence
  that matters most.
- conviction: high only if the release and evidence point clearly one way; low otherwise. Quote the deciding sentence.
"""
_SCHEMA_J = ('{"reason": "at most 40 words: the main good and bad points, weighed", '
             + _SCHEMA_R[1:-1] + ", "
             + ", ".join(f'"{k}": {{"label": "{"|".join(v[0])}", "quote": "..."}}' for k, v in FIELDS_J.items()
                         if k not in FIELDS_R) + "}")
PROMPT_J = (_HEAD + _DEFS + _DEF_R + _DEF_J + _RULES_R
            + "\nWrite \"reason\" first: weigh the evidence before you label.\nJSON: " + _SCHEMA_J)
J_GATE = {"parse_rate": 0.95, "verified_share": 0.85}  # fixed before the dev run; quality only, never returns

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


async def ask(llm: Any, system: str, user: str) -> tuple[str, bool]:
    """The reply, and whether the input overflowed the context window. Ollama refuses a prompt longer than num_ctx
    with HTTP 400 ("exceeds the available context size"); such a release counts as unparsed, so every field keeps its
    default (PLAN_60_V2 "Context overflow", fixed 2026-09-27 before arm B resumed). Other errors still stop the run."""
    try:
        return await llm(system, user), False
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400 and "context" in e.response.text:
            return "", True
        raise


def verify(raw: dict[str, Any] | None, text: str, fields: dict[str, tuple[tuple[str, ...], str]] = FIELDS
           ) -> dict[str, Any]:
    """Code has the last word: a non-default label needs a quote found word for word in the source text."""
    body = _norm(text)
    out: dict[str, Any] = {"parsed": raw is not None, "claimed": 0, "verified": 0}
    for f, (labels, default) in fields.items():
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


def research_folder(tag: str) -> Path:
    return EVIDENCE / ("events_research_Jan-v1-4B-GGUF_Q4_K_M_v3" + ("_2024" if tag == "2024" else ""))


async def extract_research() -> None:
    """Arm B: the release plus Jan's as-of evidence; quotes may come from either."""
    llm = llm_client()
    try:
        for tag, events, feats in (("2024", "data/events/events_sp500_2024.csv", "features_sp500_2024_secchk.csv"),
                                   ("2025", "data/events/events_sp500_2025.csv", "features_sp500_2025_secchk.csv")):
            out = OUT / f"llm_fields_research_{tag}.jsonl"
            done = {json.loads(x)["accession"] for x in out.read_text().splitlines()} if out.exists() else set()
            keep = set(pd.read_csv(OUT / feats)["accession"])
            rows = [r for r in pd.read_csv(BACKEND / events).itertuples() if r.accession in keep and r.accession not in done]
            print(f"{tag}: {len(rows)} releases to label with research", flush=True)
            t0 = time.monotonic()

            async def one(r: Any, tag: str = tag) -> dict[str, Any] | None:
                text, rp = text_of(r.accession), research_folder(tag) / f"{r.accession}.json"
                if text is None:
                    return None
                ev = (json.loads(rp.read_text()).get("evidence") or "")[:6000] if rp.exists() else ""
                user = (text[:6000] + "\n\n=== Research evidence (web, as of the release) ===\n"
                        + (ev or "No research evidence was found."))
                reply, overflow = await ask(llm, PROMPT_R, user)
                return {"accession": r.accession, "has_research": bool(ev), "overflow": overflow,
                        **verify(parse(reply), text[:6000] + "\n" + ev, FIELDS_R)}
            for i in range(0, len(rows), 60):
                recs = [x for x in await asyncio.gather(*(one(r) for r in rows[i:i + 60])) if x is not None]
                with out.open("a") as fh:
                    for rec in recs:
                        fh.write(json.dumps(rec) + "\n")
                n = i + len(rows[i:i + 60])
                print(f"  {n}/{len(rows)} eta={(len(rows) - n) * (time.monotonic() - t0) / n / 60:.0f}min", flush=True)
    finally:
        await llm.unload()


def llm_client_j() -> Any:
    from app.sandbox.walkforward import OllamaLLM
    return OllamaLLM("bonsai-27b:latest", base_url="http://127.0.0.1:11435", concurrency=3, num_ctx=8192,
                     num_predict=1000, cache=True, require_gpu=True)


def _user_j(acc: str, tag: str) -> tuple[str, str, bool] | None:
    text, rp = text_of(acc), research_folder(tag) / f"{acc}.json"
    if text is None:
        return None
    ev = (json.loads(rp.read_text()).get("evidence") or "")[:6000] if rp.exists() else ""
    user = text[:6000] + "\n\n=== Research evidence (web, as of the release) ===\n" + (ev or "No research evidence was found.")
    return user, text[:6000] + "\n" + ev, bool(ev)


async def _label_j(llm: Any, acc: str, tag: str) -> dict[str, Any] | None:
    u = _user_j(acc, tag)
    if u is None:
        return None
    user, source, has = u
    t0 = time.monotonic()
    reply, overflow = await ask(llm, PROMPT_J, user)
    raw = parse(reply)
    reason = str((raw or {}).get("reason", ""))[:400]
    return {"accession": acc, "has_research": has, "overflow": overflow, "s": time.monotonic() - t0, "reason": reason,
            **verify(raw, source, FIELDS_J)}


async def dev_judgement() -> bool:
    """Arm C quality gate on the same 100 dev releases as step 1: parse rate and verified quotes only."""
    ev = pd.read_csv(BACKEND / "data/events/events_sp500_2024.csv")
    rows = list(ev.sample(100, random_state=1).itertuples())
    llm = llm_client_j()
    try:
        t0 = time.monotonic()
        recs = [x for x in await asyncio.gather(*(_label_j(llm, r.accession, "2024") for r in rows)) if x]
    finally:
        await llm.unload()
    claimed = sum(r["claimed"] for r in recs)
    q = {"n": len(recs), "parse_rate": float(np.mean([r["parsed"] for r in recs])),
         "verified_share": sum(r["verified"] for r in recs) / claimed if claimed else 0.0,
         "s_per_release": (time.monotonic() - t0) / max(1, len(recs)),
         "label_counts": {f: pd.Series([r[f] for r in recs]).value_counts().to_dict() for f in FIELDS_J
                          if f not in FIELDS_R}}
    q["gate"] = J_GATE
    q["pass"] = bool(q["parse_rate"] >= J_GATE["parse_rate"] and q["verified_share"] >= J_GATE["verified_share"])
    (OUT / "llm_fields_judgement_dev.json").write_text(json.dumps(q, indent=1, default=str) + "\n")
    print(json.dumps(q, indent=1, default=str), flush=True)
    return q["pass"]


async def extract_judgement() -> None:
    """Arm C labels for the 2024 (train) and 2025-26 (test) samples; resumable."""
    llm = llm_client_j()
    try:
        for tag, events, feats in (("2024", "data/events/events_sp500_2024.csv", "features_sp500_2024_secchk.csv"),
                                   ("2025", "data/events/events_sp500_2025.csv", "features_sp500_2025_secchk.csv")):
            out = OUT / f"llm_fields_judgement_{tag}.jsonl"
            done = {json.loads(x)["accession"] for x in out.read_text().splitlines()} if out.exists() else set()
            keep = set(pd.read_csv(OUT / feats)["accession"])
            rows = [r for r in pd.read_csv(BACKEND / events).itertuples() if r.accession in keep and r.accession not in done]
            print(f"{tag}: {len(rows)} releases to label with judgement", flush=True)
            t0 = time.monotonic()
            for i in range(0, len(rows), 60):
                chunk = rows[i:i + 60]
                recs = [x for x in await asyncio.gather(*(_label_j(llm, r.accession, tag) for r in chunk)) if x]
                with out.open("a") as fh:
                    for rec in recs:
                        fh.write(json.dumps(rec) + "\n")
                n = i + len(chunk)
                print(f"  {n}/{len(rows)} eta={(len(rows) - n) * (time.monotonic() - t0) / n / 60:.0f}min", flush=True)
    finally:
        await llm.unload()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("dev", "extract", "extract-research", "test", "test-research", "dev-judgement",
                                    "extract-judgement", "test-judgement"))
    ap.add_argument("--prompt", default="")
    args = ap.parse_args()
    if args.cmd == "dev":
        asyncio.run(dev())
    elif args.cmd == "extract":
        if args.prompt not in PROMPTS:
            raise SystemExit("--prompt must be the frozen winner from `dev`")
        asyncio.run(extract(args.prompt))
    elif args.cmd == "extract-research":
        asyncio.run(extract_research())
    elif args.cmd == "dev-judgement":
        raise SystemExit(0 if asyncio.run(dev_judgement()) else 3)
    elif args.cmd == "extract-judgement":
        asyncio.run(extract_judgement())
    elif args.cmd == "test-judgement":
        from llm_fields_test import judgement_main
        judgement_main()
    elif args.cmd == "test-research":
        from llm_fields_test import research_main
        research_main()
    else:
        from llm_fields_test import main as test_main
        test_main()


if __name__ == "__main__":
    main()
