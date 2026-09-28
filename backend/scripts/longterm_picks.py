"""Long-term picks track, forward-only (docs/PLAN_60_V2.md "Long-term picks track", fixed 2026-09-28).

    python scripts/longterm_picks.py                          # make this month's cohort if due, score matured ones
    python scripts/longterm_picks.py --events results/forward/events_autodry --dir results/forward/longterm_autodry
    python scripts/longterm_picks.py --status

Once a month, on the first run at or after 16:00 ET of a new month's first weekday, Bonsai rates every S&P 500
company with a release in the last 100 days from a code-built card (PROMPT_LT); the 10 highest ratings (ties: better
12-month return vs SPY) form an equal-weight cohort entered at the next open and held 63 trading days. Each run also
scores cohorts that have matured: the basket's return from the entry open to the exit open, minus SPY's, minus 0.4%.
Cohorts and results go to a hash-chained ledger. A shadow: no money. Problems print "LEARN ALERT: ..." and never
fail the events job.
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np
import pandas as pd
from b3_evidence import outlook
from llm_fields import FIELDS_LT, PROMPT_LT, ask, parse, verify

from app.forward.ledger import Ledger
from app.forward.schedule import NY
from app.sandbox.gpu_lock import gpu_priority

TEXT = BACKEND / "data" / "events" / "text"
HISTORY = BACKEND / "data" / "events" / "events_2024-01-01_2026-09-24.csv"
PORT = 11441
N_PICKS, HOLD, COST, LOOKBACK_DAYS = 10, 63, 0.002, 100


def releases(events_dir: Path) -> pd.DataFrame:
    cols = ["cik", "ticker", "sector", "accession", "accepted_utc"]
    h = pd.read_csv(HISTORY)
    parts = [h[h["index"] == "sp500"][cols]]
    live = events_dir / "events.csv"
    if live.exists():
        f = pd.read_csv(live)
        parts.append(f[f.get("index", pd.Series("sp500", index=f.index)) == "sp500"][cols])
    ev = pd.concat(parts).drop_duplicates("accession")
    ev["t"] = pd.to_datetime(ev["accepted_utc"], utc=True, format="ISO8601")
    return ev.sort_values("t")


def _text(acc: str) -> str | None:
    p = TEXT / f"{acc}.txt.gz"
    return gzip.decompress(p.read_bytes()).decode() if p.exists() else None


def cards(ev: pd.DataFrame, now: datetime, closes: pd.DataFrame) -> list[dict[str, Any]]:
    """One card per company: its latest release in the last 100 days (text on disk), the outlook from the release
    before it, and 12-month / 1-month returns vs SPY from closes dated before today."""
    recent = ev[(ev["t"] < now) & (ev["t"] >= now - timedelta(days=LOOKBACK_DAYS))]
    px = closes[closes.index < pd.Timestamp(now.astimezone(NY).date())]
    out = []
    for cik, g in recent.groupby("cik"):
        last = g.iloc[-1]
        text = _text(last["accession"])
        if text is None:
            continue
        before = ev[(ev["cik"] == cik) & (ev["t"] < last["t"] - timedelta(days=30))
                    & (ev["t"] >= last["t"] - timedelta(days=200))]
        prev = _text(before.iloc[-1]["accession"]) if len(before) else None
        t = str(last["ticker"]).replace(".", "-")
        r12 = r1 = None
        if t in px and "SPY" in px and px[t].notna().sum() > 253:
            s, m = px[t].dropna(), px["SPY"].reindex(px[t].dropna().index)
            r12 = float((s.iloc[-1] / s.iloc[-253]) - (m.iloc[-1] / m.iloc[-253]))
            r1 = float((s.iloc[-1] / s.iloc[-22]) - (m.iloc[-1] / m.iloc[-22]))
        ret = ("12-month return vs S&P 500: " + (f"{r12:+.1%}" if r12 is not None else "n/a")
               + "; last month vs S&P 500: " + (f"{r1:+.1%}" if r1 is not None else "n/a"))
        o = outlook(prev, 1500) if prev else ""
        body = (f"Company: {t} ({last['sector']})\n{ret}\n\n=== Latest earnings release "
                f"({str(last['accepted_utc'])[:10]}) ===\n{text[:4000]}\n\n=== Outlook given the quarter before ===\n"
                + (o or "Not found."))
        out.append({"ticker": t, "cik": int(str(cik)), "accession": last["accession"], "r12": r12, "card": body,
                    "source": body})
    return out


async def rate(llm: Any, cs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    async def one(c: dict[str, Any]) -> dict[str, Any]:
        reply, overflow = await ask(llm, PROMPT_LT, c["card"])
        raw = parse(reply)
        v = verify(raw, c["source"], FIELDS_LT)
        return {"ticker": c["ticker"], "accession": c["accession"], "r12": c["r12"], "rating": int(v["outlook_6m"]),
                "parsed": v["parsed"], "overflow": overflow, "reason": str((raw or {}).get("reason", ""))[:300]}
    return list(await asyncio.gather(*(one(c) for c in cs)))


def choose(rated: list[dict[str, Any]], n: int = N_PICKS) -> list[dict[str, Any]]:
    return sorted(rated, key=lambda x: (-x["rating"], -(x["r12"] if x["r12"] is not None else -9)))[:n]


def due(recs: list[dict[str, Any]], now: datetime) -> bool:
    et = now.astimezone(NY)
    month = et.strftime("%Y-%m")
    if any(r.get("type") == "cohort" and r["month"] == month for r in recs):
        return False
    first_weekday = date(et.year, et.month, 1)
    while first_weekday.weekday() >= 5:
        first_weekday += timedelta(days=1)
    late = first_weekday < et.date() <= first_weekday + timedelta(days=7)  # a missed first day: catch up that week
    return late or (et.date() == first_weekday and et.hour >= 16)


def score(recs: list[dict[str, Any]], opens: pd.DataFrame) -> list[dict[str, Any]]:
    """Results for cohorts whose exit open (entry + 63 trading days) is in the data and not yet scored."""
    done = {r["month"] for r in recs if r.get("type") == "result"}
    days = pd.DatetimeIndex(opens.index)
    out = []
    for c in recs:
        if c.get("type") != "cohort" or c["month"] in done:
            continue
        i = int(days.searchsorted(pd.Timestamp(c["made_on"]) + pd.Timedelta(days=1)))
        if i + HOLD >= len(days):
            continue
        rets = [float(opens[t].iloc[i + HOLD] / opens[t].iloc[i] - 1) for t in c["tickers"]
                if t in opens and pd.notna(opens[t].iloc[i]) and pd.notna(opens[t].iloc[i + HOLD])]
        spy = float(opens["SPY"].iloc[i + HOLD] / opens["SPY"].iloc[i] - 1)
        basket = float(np.mean(rets)) if rets else 0.0
        out.append({"month": c["month"], "entry": days[i].date().isoformat(), "exit": days[i + HOLD].date().isoformat(),
                    "basket": round(basket, 5), "spy": round(spy, 5), "priced": len(rets),
                    "excess_net": round(basket - spy - 2 * COST, 5)})
    return out


def prices(tickers: set[str], now: datetime) -> tuple[pd.DataFrame, pd.DataFrame]:
    import yfinance as yf  # type: ignore[import-untyped]
    df = yf.download(sorted(tickers | {"SPY"}), start=(now - timedelta(days=560)).date().isoformat(),
                     end=now.astimezone(NY).date().isoformat(), auto_adjust=True, progress=False, threads=True)
    o, c = df["Open"], df["Close"]
    return (o.to_frame("SPY") if isinstance(o, pd.Series) else o), (c.to_frame("SPY") if isinstance(c, pd.Series) else c)


def status(recs: list[dict[str, Any]]) -> dict[str, Any]:
    res = [r for r in recs if r.get("type") == "result"]
    x = np.array([r["excess_net"] for r in res])
    lo80 = None
    if len(x) > 1:
        b = x[np.random.default_rng(0).integers(0, len(x), (5000, len(x)))].mean(1)
        lo80 = round(float(np.percentile(b, 20)), 5)
    return {"cohorts": sum(r.get("type") == "cohort" for r in recs), "scored": len(res),
            "mean_excess_net": round(float(x.mean()), 5) if len(x) else None, "lo80": lo80,
            "ready_to_judge": len(res) >= 12,
            "latest": next((r["tickers"] for r in reversed(recs) if r.get("type") == "cohort"), None)}


def run(events_dir: Path, out: Path, now: datetime, use_gpu: bool) -> None:
    from forward_events import Ollama, wait_gpu_free

    from app.sandbox.walkforward import OllamaLLM
    out.mkdir(parents=True, exist_ok=True)
    led = Ledger(out / "ledger.jsonl")
    recs = led.verify() if (out / "ledger.jsonl").exists() else []
    make = due(recs, now)
    cohort_tickers = {t for r in recs if r.get("type") == "cohort" for t in r["tickers"]}
    if not make and not any(r.get("type") == "cohort" for r in recs):
        print("long-term picks: no cohort due yet")
        return
    ev = releases(events_dir)
    recent = ev[ev["t"] >= now - timedelta(days=LOOKBACK_DAYS)]
    tickers = {str(t).replace(".", "-") for t in recent["ticker"]} if make else set()
    opens, closes = prices(tickers | cohort_tickers, now)
    for r in score(recs, opens):
        led.append("result", **r)
        print(f"long-term picks: cohort {r['month']} closed, {r['excess_net']:+.2%} vs SPY after costs")
    if not make:
        return
    if not use_gpu:
        print("LEARN ALERT: long-term picks: a cohort is due but the GPU is off; it will be made next run")
        return
    cs = cards(ev, now, closes)
    with gpu_priority("longterm_picks"):
        if not wait_gpu_free(900):
            print("LEARN ALERT: long-term picks: the GPU stayed busy; the cohort will be made next run")
            return
        srv = Ollama(PORT, str(Path.home() / ".ollama" / "models"), 3, out / "ollama.log")
        try:
            llm = OllamaLLM("bonsai-27b:latest", base_url=f"http://127.0.0.1:{PORT}", concurrency=3, num_ctx=8192,
                            num_predict=600, cache=False, require_gpu=True)

            async def go() -> list[dict[str, Any]]:
                try:
                    return await rate(llm, cs)
                finally:
                    await llm.unload()
            rated = asyncio.run(go())
        finally:
            srv.stop()
    picks = choose(rated)
    (out / f"ratings_{now.astimezone(NY).strftime('%Y-%m')}.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rated))
    led.append("cohort", month=now.astimezone(NY).strftime("%Y-%m"), made_on=now.astimezone(NY).date().isoformat(),
               made_at=now.isoformat(timespec="seconds"), tickers=[p["ticker"] for p in picks],
               ratings=[p["rating"] for p in picks], candidates=len(cs),
               rating_counts={str(k): int(v) for k, v in pd.Series([r["rating"] for r in rated]).value_counts().items()})
    print(f"long-term picks: cohort {now.astimezone(NY).strftime('%Y-%m')} from {len(cs)} cards: "
          + ", ".join(f"{p['ticker']}({p['rating']})" for p in picks))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--events", default="results/forward/events")
    ap.add_argument("--dir", default="results/forward/longterm")
    ap.add_argument("--no-gpu", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    out = BACKEND / a.dir
    try:
        if not a.status:
            run(BACKEND / a.events, out, datetime.now(UTC), not a.no_gpu)
        recs = Ledger(out / "ledger.jsonl").verify() if (out / "ledger.jsonl").exists() else []
        print("long-term picks (shadow):", json.dumps(status(recs)))
    except Exception as e:  # noqa: BLE001 - a shadow: never fail the events job
        print(f"LEARN ALERT: long-term picks failed: {type(e).__name__}: {e}"[:300])


if __name__ == "__main__":
    main()
