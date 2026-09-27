"""8-K breaking-news watcher, W1 (docs/PLAN_60_V2.md "8-K breaking-news watcher"; spec fixed before any text was read).

    python scripts/news8k.py fetch     # SEC text of each filing + its press-release exhibit (network, no GPU)
    python scripts/news8k.py dev       # quality gate on 100 filings from 2024 (exit 3 = gate failed)
    python scripts/news8k.py extract   # Bonsai labels every filing (GPU; resumable)
    python scripts/news8k.py test      # the one pre-registered return test

Bonsai reads the filing and says whether it is good, neutral or bad news for the stock over the next week, and how
big, each with an exact quote. Code keeps a non-neutral label only if its quote is in the text, and scores
direction (+1/0/-1) x (2 if major else 1). Nothing is fitted.
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
from llm_fields import parse, verify

from app.tools.extract import html_to_text
from app.tools.gateway import _read_env_file

EVENTS = BACKEND / "data" / "events" / "news8k_2024-01-01_2026-09-24.csv"
TEXT = BACKEND / "data" / "events" / "text8k"
OUT = BACKEND / "results" / "events"
MAX_CHARS = 6000
FIELDS = {"direction": (("positive", "neutral", "negative"), "neutral"), "size": (("major", "minor"), "minor")}
GATE = {"parse_rate": 0.95, "verified_share": 0.85}
COST = 0.004
PROMPT = """You read one SEC Form 8-K (a company's report of a material event) and judge it for a portfolio manager.
Definitions:
- direction: is this news good (positive) or bad (negative) for the company's stock over the NEXT WEEK compared with
  its sector, as a skeptical analyst would judge it? neutral if routine (ordinary credit agreements, planned
  successions, board housekeeping) or mixed. Quote the sentence that decides it.
- size: major if it changes the business materially (a large acquisition or divestiture, CEO or CFO leaving
  unexpectedly, a restatement, big layoffs or impairments, a delisting notice); minor otherwise. Quote it.
Rules: use ONLY the filing text. For every label other than neutral / minor, copy the sentence that shows it word for
word as "quote" (at most 30 words); otherwise quote "". Never guess.
Write "reason" first (at most 40 words: what happened and why it matters), then the labels.
Reply with ONLY one JSON object on one line.
JSON: {"reason": "...", "direction": {"label": "positive|neutral|negative", "quote": "..."}, "size": {"label": "major|minor", "quote": "..."}}"""


def text_path(acc: str) -> Path:
    return TEXT / f"{acc}.txt.gz"


ITEM = re.compile(r"Item\s+[1-9]\.\d\d", re.IGNORECASE)
COVER_END = re.compile(r"(extended transition period|emerging growth company)[^\n]*\n", re.IGNORECASE)


def text_of(acc: str) -> str | None:
    """The filing from its first "Item x.xx" heading on (the cover page before it is boilerplate), 6,000 chars. When
    the heading was lost in the HTML conversion, from the end of the cover's check-box boilerplate instead."""
    p = text_path(acc)
    if not p.exists():
        return None
    t = gzip.decompress(p.read_bytes()).decode()
    m = ITEM.search(t)
    if m:
        return t[m.start():][:MAX_CHARS]
    ends = [c.end() for c in COVER_END.finditer(t[:6000])]  # headings lost in the HTML: skip the check-box cover
    return t[ends[-1] if ends else 0:][:MAX_CHARS]


async def fetch() -> None:
    from build_events import Sec
    ev = pd.read_csv(EVENTS)
    TEXT.mkdir(parents=True, exist_ok=True)
    todo = [r for r in ev.itertuples() if not text_path(r.accession).exists()]
    print(f"{len(ev)} filings, {len(todo)} to download", flush=True)
    sec = Sec(_read_env_file(BACKEND / ".env")["SEC_USER_AGENT"])
    sem = asyncio.Semaphore(4)
    t0, done = time.monotonic(), 0

    async def page(url: str) -> str:
        if not isinstance(url, str) or not url:
            return ""
        async with sem:
            r = await sec.get(url)
        if r is None:
            return ""
        return str(html_to_text(r.text, 200_000)["text"]) if "htm" in url.lower() else r.text

    async def one(r: Any) -> None:
        nonlocal done
        body = await page(r.primary_url)
        ex = await page(r.ex99_url) if r.ex99_url != r.primary_url else ""
        text = body + ("\n\n=== Press release (exhibit 99) ===\n" + ex if ex else "")
        if text.strip():
            text_path(r.accession).write_bytes(gzip.compress(text.encode()))
        done += 1
        if done % 500 == 0:
            print(f"  {done}/{len(todo)} {time.monotonic() - t0:.0f}s", flush=True)
    for i in range(0, len(todo), 200):
        await asyncio.gather(*(one(r) for r in todo[i:i + 200]))
    await sec.client.aclose()
    print(f"texts: {sum(1 for r in ev.itertuples() if text_path(r.accession).exists())}/{len(ev)}", flush=True)


def llm_client() -> Any:
    from app.sandbox.walkforward import OllamaLLM
    # its own port: the autonomy runner's event job may start Ollama on 11435 when the GPU is free
    return OllamaLLM("bonsai-27b:latest", base_url="http://127.0.0.1:11438", concurrency=3, num_ctx=8192,
                     num_predict=500, cache=True, require_gpu=True)


def score(rec: dict[str, Any]) -> float:
    d = {"positive": 1.0, "neutral": 0.0, "negative": -1.0}[rec["direction"]]
    return d * (2.0 if rec["size"] == "major" else 1.0)


async def label(llm: Any, acc: str) -> dict[str, Any] | None:
    text = text_of(acc)
    if text is None:
        return None
    t0 = time.monotonic()
    raw = parse(await llm(PROMPT, text))
    rec = {"accession": acc, "s": time.monotonic() - t0, "reason": str((raw or {}).get("reason", ""))[:400],
           **verify(raw, text, FIELDS)}
    rec["score"] = score(rec)
    return rec


async def dev() -> bool:
    ev = pd.read_csv(EVENTS)
    rows = ev[ev["filed"] < "2025-01-01"].sample(100, random_state=1)
    llm = llm_client()
    try:
        t0 = time.monotonic()
        recs = [x for x in await asyncio.gather(*(label(llm, a) for a in rows["accession"])) if x]
    finally:
        await llm.unload()
    claimed = sum(r["claimed"] for r in recs)
    q: dict[str, Any] = {"n": len(recs), "parse_rate": float(np.mean([r["parsed"] for r in recs])),
                         "verified_share": sum(r["verified"] for r in recs) / claimed if claimed else 0.0,
                         "s_per_filing": (time.monotonic() - t0) / max(1, len(recs)),
                         "direction": pd.Series([r["direction"] for r in recs]).value_counts().to_dict(),
                         "size": pd.Series([r["size"] for r in recs]).value_counts().to_dict(), "gate": GATE}
    q["pass"] = bool(q["parse_rate"] >= GATE["parse_rate"] and q["verified_share"] >= GATE["verified_share"])
    (OUT / "news8k_dev.json").write_text(json.dumps(q, indent=1) + "\n")
    print(json.dumps(q, indent=1), flush=True)
    return bool(q["pass"])


async def extract() -> None:
    out = OUT / "news8k_labels.jsonl"
    done = {json.loads(x)["accession"] for x in out.read_text().splitlines()} if out.exists() else set()
    accs = [a for a in pd.read_csv(EVENTS)["accession"] if a not in done and text_path(a).exists()]
    print(f"{len(accs)} filings to label", flush=True)
    llm = llm_client()
    t0 = time.monotonic()
    try:
        for i in range(0, len(accs), 60):
            recs = [x for x in await asyncio.gather(*(label(llm, a) for a in accs[i:i + 60])) if x]
            with out.open("a") as fh:
                for r in recs:
                    fh.write(json.dumps(r) + "\n")
            n = i + len(accs[i:i + 60])
            print(f"  {n}/{len(accs)} eta={(len(accs) - n) * (time.monotonic() - t0) / n / 60:.0f}min", flush=True)
    finally:
        await llm.unload()


def spread_net(df: pd.DataFrame, n_boot: int = 5000) -> dict[str, float | int | None]:
    s = []
    for _, g in df.groupby("month"):
        up, dn = g.loc[g["score"] > 0, "fwd5"], g.loc[g["score"] < 0, "fwd5"]
        if len(up) >= 3 and len(dn) >= 3:
            s.append(float(up.mean() - dn.mean()) - COST)
    if not s:
        return {"months": 0, "net_pct": None, "ci90_lo": None, "ci90_hi": None}
    a = np.array(s)
    boots = a[np.random.default_rng(0).integers(0, len(a), (n_boot, len(a)))].mean(1)
    lo, hi = np.percentile(boots, [5, 95])
    return {"months": len(a), "net_pct": round(100 * float(a.mean()), 3), "ci90_lo": round(100 * float(lo), 3),
            "ci90_hi": round(100 * float(hi), 3)}


def test() -> None:
    from event_eval import build

    from app.sandbox.dsr import register
    from app.sandbox.events import Prices, monthly_ic
    p = Prices.from_long(pd.read_parquet(BACKEND / "data/events/ohlcv_2023-01-01_2026-09-25.parquet"))
    ev = pd.read_csv(EVENTS).assign(index="sp500")
    df = build(ev, p)
    lab = pd.DataFrame([json.loads(x) for x in (OUT / "news8k_labels.jsonl").read_text().splitlines()])
    d = df[df["scorable"]].merge(lab[["accession", "score", "direction", "size"]], on="accession").merge(
        ev[["accession", "category", "filed"]], on="accession")
    d["period"] = np.where(d["filed"] < "2025-01-01", "2024", "2025-26")
    out: dict[str, Any] = {"events": len(d), "labels": d["direction"].value_counts().to_dict(),
                           "pooled": {"ic": monthly_ic(d, "score", "fwd5", min_n=10), "spread_net": spread_net(d),
                                      "ic_fwd20": monthly_ic(d, "score", "fwd20", min_n=10)},
                           "periods": {k: monthly_ic(g, "score", "fwd5", min_n=10) for k, g in d.groupby("period")},
                           "by_category": {k: monthly_ic(g, "score", "fwd5", min_n=5) for k, g in d.groupby("category")}}
    pooled = out["pooled"]
    out["checks"] = {"pooled_ic_ci_above_0": bool((pooled["ic"]["ci_lo"] or 0) > 0),
                     "positive_each_period": bool(all((v["mean_ic"] or 0) > 0 for v in out["periods"].values())),
                     "net_spread_ci90_above_0": bool((pooled["spread_net"]["ci90_lo"] or 0) > 0)}
    out["pass"] = all(out["checks"].values())
    (OUT / "news8k_test.json").write_text(json.dumps(out, indent=1) + "\n")
    register({"trial": "news8k_bonsai_direction", "date": time.strftime("%Y-%m-%d"), "kind": "signal_ic",
              "ic": pooled["ic"]["mean_ic"], "result": "pass" if out["pass"] else "fail"})
    print(json.dumps(out, indent=1))
    print("verdict (pre-registered):", "PASS" if out["pass"] else "FAIL")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("fetch", "dev", "extract", "test"))
    a = ap.parse_args()
    if a.cmd == "fetch":
        asyncio.run(fetch())
    elif a.cmd == "dev":
        raise SystemExit(0 if asyncio.run(dev()) else 3)
    elif a.cmd == "extract":
        asyncio.run(extract())
    else:
        test()


if __name__ == "__main__":
    main()
