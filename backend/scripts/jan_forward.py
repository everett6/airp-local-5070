"""The app's explicit Jan research → Bonsai paper input path. No strategy registration."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import pandas as pd

from app.forward.ledger import jsonl_records, write_atomic
from app.sandbox.agent_worker import RESEARCH_SYSTEM_ASOF, brief_request, finish_brief
from app.sandbox.events import Prices
from app.sandbox.gpu_lock import gpu_job
from app.sandbox.walkforward import OllamaLLM
from app.tools.gateway import ToolGateway
from app.tools.netguard import SafeFetcher

JAN = "hf.co/janhq/Jan-v1-4B-GGUF:Q4_K_M"
MODELS = str(Path.home() / ".ollama" / "models")
PIPELINE = "jan_bonsai_v1"
RESERVE_S = 180


class RecordingModel:
    """Capture the exact logical requests and replies used by the jailed researcher."""

    def __init__(self, llm: OllamaLLM) -> None:
        self.llm = llm
        self.num_ctx, self.num_predict = llm.num_ctx, llm.num_predict
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, system: str, user: str, mode: str | None = None) -> str:
        reply = await (self.llm(system, user, mode=mode) if mode else self.llm(system, user))
        self.calls.append({"system": system, "user": user, "mode": mode, "reply": reply})
        return cast(str, reply)


def sha(body: str | bytes) -> str:
    return hashlib.sha256(body.encode() if isinstance(body, str) else body).hexdigest()


def checked_lines(rec: dict[str, Any]) -> list[str]:
    """Citations and numbers were checked by finish_brief; this is not human verification."""
    brief = rec.get("brief") or {}
    if not brief.get("parsed") or brief.get("truncated"):
        return []
    facts = [f for f in brief.get("facts", []) if isinstance(f, dict) and f.get("text") and f.get("source")
             and (not f.get("date") or str(f["date"])[:10] <= str(rec["as_of"])[:10])]
    if not facts:
        return []
    lines = ["Jan research (sources available at filing acceptance; citations and numbers checked by code):"]
    lines += [f"- {str(f['text'])[:300]} [{f['source']}] ({str(f.get('date', ''))[:10]})" for f in facts[:6]]
    for key in ("risks", "catalysts", "missing"):
        items = [str(x)[:200] for x in brief.get(key, []) if x][:3]
        if items:
            lines.append(f"Jan {key} (model assessment): " + "; ".join(items))
    return lines


async def research_one(r: Any, llm: OllamaLLM, fetcher: SafeFetcher, ua: str, lookup: Any,
                       prefetch: list[dict[str, Any]]) -> dict[str, Any]:
    from research_events import Router, research
    recorder = RecordingModel(llm)
    async with asyncio.timeout(120):
        rec: dict[str, Any] = await research(r, Router(cast(OllamaLLM, recorder), None), fetcher, ua, lookup, 2, prefetch,
                                             cap_s=20, brief=False, horizon_days=5)
        if rec.get("error") or not rec.get("evidence"):
            return rec
        system, user, tags = brief_request({"ticker": r.ticker, "as_of": rec["as_of"],
                                            "horizon_days": 5}, rec["evidence"])
        reply = await recorder(system, user)
        rec.update(brief=finish_brief(reply, tags, rec["evidence"]),
                   prompts={"research_system": RESEARCH_SYSTEM_ASOF, "brief_system": system,
                            "brief_user": user}, brief_reply=reply, llm_calls=recorder.calls)
        return rec


async def gather(feats: Path, events: Path, p: Prices, d: Path, clock: Callable[[], datetime],
                 digest: str, extract_path: Path | None = None) -> tuple[Path, dict[str, str], dict[str, dict[str, str]]]:
    from extract_events import text_path
    from forward_events import Ollama, entry_deadline
    from research_events import lookup_from, prefetch_for, previous_releases

    ft = pd.read_csv(feats)
    extracts = {str(x["accession"]): x for x in jsonl_records(extract_path or d / "jan" / "extract.jsonl")}
    ev = pd.read_csv(events)
    urls = dict(zip(ev["accession"], ev["ex99_url"].fillna(""), strict=True))
    prev = previous_releases(ev)
    out = d / "jan" / "research"
    out.mkdir(parents=True, exist_ok=True)
    skip: dict[str, str] = {}
    provenance: dict[str, dict[str, str]] = {}
    ready: list[dict[str, Any]] = []
    # Do not start a server or read gateway configuration when nothing can be researched.
    eligible_rows: list[Any] = []
    for row in ft.itertuples(index=False):
        accession = str(row.accession)
        extracted = extracts.get(accession)
        if not extracted or extracted.get("model") != JAN or not extracted.get("parsed"):
            skip[accession] = "Jan extraction failed or unavailable"
        elif (entry_deadline(str(row.accepted_utc)) - clock()).total_seconds() <= RESERVE_S:
            skip[accession] = "Jan research deferred: insufficient time before entry deadline"
        else:
            eligible_rows.append(row)
    enriched = feats.with_name(feats.stem + "_researched.csv")
    if not eligible_rows:
        write_atomic(enriched, ft.iloc[:0].to_csv(index=False))
        return enriched, skip, provenance
    llm = OllamaLLM(JAN, base_url="http://127.0.0.1:11436", concurrency=1,
                    num_ctx=8192, num_predict=1200, cache=False, require_gpu=True)
    base = ToolGateway.from_env("as_of", as_of=datetime(2000, 1, 1, tzinfo=UTC))
    fetcher = SafeFetcher(base.fetcher.user_agent, timeout_s=20, total_timeout_s=40)
    ua = base.sec_user_agent
    llm.native_tools = base.specs_native()
    await base.aclose()
    try:
        if not ua:
            raise RuntimeError("SEC user agent is unavailable")
        with gpu_job("app Jan research"):
            srv = Ollama(11436, MODELS, 1, d / "jan" / "ollama.log")
            try:
                r: Any
                for r in eligible_rows:
                    acc = str(r.accession)
                    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", acc):
                        raise ValueError("unsafe filing accession")
                    deadline = entry_deadline(str(r.accepted_utc))
                    if (deadline - clock()).total_seconds() <= RESERVE_S:
                        skip[acc] = "Jan research deferred: insufficient time before entry deadline"
                        continue
                    if ready and (min(entry_deadline(str(x["accepted_utc"])) for x in ready)
                                  - clock()).total_seconds() <= RESERVE_S:
                        skip[acc] = "Jan research deferred: preserving time to judge completed research"
                        continue
                    try:
                        rec = await research_one(r, llm, fetcher, ua, lookup_from(p),
                                                 prefetch_for(r, urls, {}, prev))
                        lines = checked_lines(rec)
                        if not lines or clock() >= deadline:
                            skip[acc] = "Jan research unavailable, unusable, or completed after entry deadline"
                            continue
                        source = text_path(acc)
                        rec.update(model=JAN, digest=digest, pipeline=PIPELINE,
                                   written_at=clock().isoformat(), base_fact_sheet=r.fact_sheet,
                                   source_sha256=sha(source.read_bytes()),
                                   settings={"num_ctx": 8192, "num_predict": 1200, "parallel": 1,
                                             "require_gpu": True, "temperature": 0, "max_rounds": 2, "timeout_s": 120},
                                   implementation_sha256=sha(Path(__file__).read_bytes()))
                        body = json.dumps(rec, indent=1, default=str) + "\n"
                        path = out / f"{acc}.json"
                        # Each attempt is archived separately so failed runs cannot overwrite decision evidence.
                        path = path.with_name(f"{acc}-{sha(body)[:16]}.json")
                        write_atomic(path, body)
                        provenance[acc] = {"research_path": str(path.relative_to(d)),
                                           "research_sha256": sha(body)}
                        ready.append({**r._asdict(), "fact_sheet": str(r.fact_sheet) + "\n" + "\n".join(lines)})
                        print(f"Jan research saved: {r.ticker} {acc}, {len(lines) - 1} research lines", flush=True)
                    except (TimeoutError, OSError, ValueError, RuntimeError, httpx.HTTPError) as exc:
                        skip[acc] = f"Jan research failed: {type(exc).__name__}"
                        print(f"{r.ticker} {acc}: {skip[acc]}", flush=True)
            finally:
                try:
                    await llm.unload()
                finally:
                    srv.stop()
    finally:
        await fetcher.aclose()
    write_atomic(enriched, pd.DataFrame(ready, columns=ft.columns).to_csv(index=False))
    return enriched, skip, provenance


def prepare(d: Path, events: Path, since: date, tag: str, prices: Path, p: Prices,
            clock: Callable[[], datetime], activity: list[str] | None = None) -> tuple[Path, dict[str, str], dict[str, dict[str, str]]]:
    from evidence_bundle import manifest_digest
    from extract_events import text_path
    from forward_events import BACKEND, PY, SHEET_VERSION, Ollama, entry_deadline, run, stage
    digest = manifest_digest(JAN, MODELS)
    if not digest or not manifest_digest("bonsai-27b:latest", MODELS):
        raise RuntimeError("Jan and Bonsai must already be installed; no model is downloaded")
    jan = d / "jan"
    jan.mkdir(parents=True, exist_ok=True)
    # Expired releases cannot become paper decisions and do not consume model time.
    ev = pd.read_csv(events)
    ev = ev.loc[[entry_deadline(str(a)) > clock() for a in ev["accepted_utc"]]]
    if ev.empty:
        raise RuntimeError("No filings remain before their entry deadline")
    eligible = jan / "events_new.csv"
    write_atomic(eligible, ev.to_csv(index=False))
    ex = jan / f"extract_{tag}.jsonl"
    with stage("download"):
        run([PY, "scripts/extract_events.py", "fetch", "--events", str(eligible), "--from", since.isoformat()])
    ev = ev.loc[[text_path(str(a)).exists() for a in ev["accession"]]]
    if ev.empty:
        raise RuntimeError("No downloadable releases; neither model ran")
    write_atomic(eligible, ev.to_csv(index=False))
    with stage("Jan extraction"):
        srv = Ollama(11437, MODELS, 1, jan / "ollama.log")
        try:
            run([PY, "scripts/extract_events.py", "extract", "--events", str(eligible), "--from", since.isoformat(),
                 "--to", "2099-12-31", "--model", JAN, "--base-url", "http://127.0.0.1:11437", "--parallel", "1",
                 "--out", str(ex)])
        finally:
            if activity is not None and jsonl_records(ex):
                activity.append(JAN)
            srv.stop()
    with stage("fact sheet"):
        run([PY, "scripts/build_features.py", "--events", str(eligible), "--extract", str(ex), "--name", tag,
             "--prices", str(prices), "--live", "--sheet-version", str(SHEET_VERSION)])
    feats = BACKEND / "results" / "events" / f"features_{tag}.csv"
    with stage("Jan research"):
        return asyncio.run(gather(feats, d / "events.csv", p, d, clock, digest, ex))
