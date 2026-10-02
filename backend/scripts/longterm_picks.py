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
import hashlib
import json
import math
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np
import pandas as pd
from b3_evidence import outlook
from llm_fields import (
    FIELDS_LT,
    PROMPT_LT,
    ask,
    parse,
    rating_score,
    theses,
    verify,
)

from app.data_ingestion.tickers import trading_symbol
from app.forward.ledger import Ledger, jsonl_records, open_append
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
        t = trading_symbol(last["ticker"])
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


def card_key(c: dict[str, Any]) -> str:
    return hashlib.sha256(f"{c['ticker']}|{c['accession']}|{c['card']}".encode()).hexdigest()[:16]


async def rate(llm: Any, cs: list[dict[str, Any]], partial: Path | None = None) -> list[dict[str, Any]]:
    """Rate every card. With `partial`, each rating is saved as it is made, and a run that was cut off (1 Oct 2026:
    449 of 490 ratings lost to a restart) picks up where it stopped: a saved rating is reused only when its card is
    exactly the one the model would be shown again."""
    kept = {r["card_key"]: r for r in jsonl_records(partial) if "card_key" in r} if partial else {}

    async def one(c: dict[str, Any]) -> dict[str, Any]:
        key = card_key(c)
        if key in kept:
            return {k: v for k, v in kept[key].items() if k != "card_key"}
        reply, overflow = await ask(llm, PROMPT_LT, c["card"])
        raw = parse(reply)
        v = verify(raw, c["source"], FIELDS_LT)
        rating = int(v["outlook_6m"])
        ev, probs = await rating_score(llm, PROMPT_LT, c["card"], reply, "outlook_6m", raw, rating)
        r = {"ticker": c["ticker"], "accession": c["accession"], "r12": c["r12"], "rating": rating,
             "score": round(ev, 4), "probs": probs,
             "parsed": v["parsed"], "overflow": overflow, "reason": str((raw or {}).get("reason", ""))[:300],
             **theses(raw, c["source"])}
        if partial:
            with open_append(partial) as f:
                f.write(json.dumps({**r, "card_key": key}) + "\n")
        return r
    if kept:
        print(f"long-term picks: {sum(card_key(c) in kept for c in cs)} of {len(cs)} ratings kept from a cut-off run")
    return list(await asyncio.gather(*(one(c) for c in cs)))


def choose(rated: list[dict[str, Any]], n: int = N_PICKS) -> list[dict[str, Any]]:
    return sorted(rated, key=lambda x: (-x.get("score", x["rating"]), -(x["r12"] if x["r12"] is not None else -9)))[:n]


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
    if "SPY" not in opens:
        raise ValueError("long-term cohort: SPY open prices missing")
    days = pd.DatetimeIndex(opens.index[opens["SPY"].notna()])
    out = []
    for c in recs:
        if c.get("type") != "cohort" or c["month"] in done:
            continue
        i = int(days.searchsorted(pd.Timestamp(c["made_on"]) + pd.Timedelta(days=1)))
        if i + HOLD >= len(days):
            continue
        entry, exit_ = days[i], days[i + HOLD]

        def price(t: str, day: pd.Timestamp) -> float:
            return cast(float, opens.at[day, t])

        missing = [t for t in c["tickers"] if t not in opens or not math.isfinite(price(t, entry))
                   or not math.isfinite(price(t, exit_)) or price(t, entry) <= 0 or price(t, exit_) <= 0]
        if missing:
            raise ValueError(f"long-term cohort {c['month']}: missing entry/exit opens for {', '.join(missing)}")
        rets = [price(t, exit_) / price(t, entry) - 1 for t in c["tickers"]]
        spy = price("SPY", exit_) / price("SPY", entry) - 1
        basket = float(np.mean(rets))
        out.append({"month": c["month"], "entry": days[i].date().isoformat(), "exit": days[i + HOLD].date().isoformat(),
                    "basket": round(basket, 5), "spy": round(spy, 5), "priced": len(rets),
                    "excess_net": round(basket - spy - 2 * COST, 5)})
    return out


def score_each(recs: list[dict[str, Any]], opens: pd.DataFrame) -> list[dict[str, Any]]:
    """`score`, cohort by cohort. A cohort that cannot be scored (a pick stopped trading before its exit: bought
    out or delisted) is reported and left unscored: it must not stop the other cohorts' results or this month's new
    cohort. How to price such a pick is a rule for the user to set; no result is invented here."""
    if "SPY" not in opens:
        raise ValueError("long-term cohort: SPY open prices missing")
    results = [r for r in recs if r.get("type") == "result"]
    out: list[dict[str, Any]] = []
    for c in recs:
        if c.get("type") != "cohort":
            continue
        try:
            out += score([*results, c], opens)
        except ValueError as e:
            print(f"LEARN ALERT: {e}; that cohort stays unscored until its pricing is decided")
    return out


def prices(tickers: set[str], now: datetime) -> tuple[pd.DataFrame, pd.DataFrame]:
    import yfinance as yf  # type: ignore[import-untyped]
    df = yf.download(sorted(tickers | {"SPY"}), start=(now - timedelta(days=560)).date().isoformat(),
                     end=now.astimezone(NY).date().isoformat(), auto_adjust=True, progress=False, threads=True)
    o, c = df["Open"], df["Close"]
    return (o.to_frame("SPY") if isinstance(o, pd.Series) else o), (c.to_frame("SPY") if isinstance(c, pd.Series) else c)


def expected_months(now: datetime) -> list[str]:
    """Months whose first-weekday 16:00 ET cohort deadline has passed."""
    et = now.astimezone(NY)
    out = []
    y, m = 2026, 10
    while (y, m) <= (et.year, et.month):
        first = date(y, m, 1)
        while first.weekday() >= 5:
            first += timedelta(days=1)
        if et >= datetime.combine(first, datetime.min.time(), NY).replace(hour=16):
            out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def status(recs: list[dict[str, Any]], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    cohorts = [r for r in recs if r.get("type") == "cohort"]
    results = [r for r in recs if r.get("type") == "result"]
    by_month = {r["month"]: r for r in cohorts}
    issues = []
    if len(by_month) != len(cohorts):
        issues.append("duplicate cohort month")
    for c in cohorts:
        names = c.get("tickers", [])
        if len(names) != N_PICKS or len(set(names)) != N_PICKS:
            issues.append(f"{c['month']}: expected {N_PICKS} distinct picks")
    for r in results:
        if r["month"] not in by_month or r.get("priced") != N_PICKS or not math.isfinite(
                float(r.get("excess_net", float("nan")))):
            issues.append(f"{r['month']}: invalid result or missing cohort")
    scored_months = {r["month"] for r in results}
    if len(scored_months) != len(results):
        issues.append("duplicate result month")
    missing = sorted(set(expected_months(now)) - set(by_month))
    overdue = [c["month"] for c in cohorts if c["month"] not in scored_months and
               (now.astimezone(NY).date() - date.fromisoformat(c["made_on"])).days > 110]
    valid = [r for r in results if r["month"] in by_month and r.get("priced") == N_PICKS and
             math.isfinite(float(r.get("excess_net", float("nan"))))]
    x = np.array([r["excess_net"] for r in valid])
    lo80 = None
    if len(x) > 1:
        b = x[np.random.default_rng(0).integers(0, len(x), (5000, len(x)))].mean(1)
        lo80 = round(float(np.percentile(b, 20)), 5)
    return {"cohorts": len(cohorts), "scored": len(results), "pending": sorted(set(by_month) - scored_months),
            "missing_months": missing, "overdue_results": overdue, "issues": issues,
            "mean_excess_net": round(float(x.mean()), 5) if len(x) else None, "lo80": lo80,
            "ready_to_judge": len(valid) >= 12 and not issues and not missing and not overdue,
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
    tickers = {trading_symbol(t) for t in recent["ticker"]} if make else set()
    opens, closes = prices(tickers | cohort_tickers, now)
    for r in score_each(recs, opens):
        led.append("result", **r)
        print(f"long-term picks: cohort {r['month']} closed, {r['excess_net']:+.2%} vs SPY after costs")
    if not make:
        return
    if not use_gpu:
        print("LEARN ALERT: long-term picks: a cohort is due but the GPU is off; it will be made next run")
        return
    cs = cards(ev, now, closes)
    partial = out / f"ratings_{now.astimezone(NY).strftime('%Y-%m')}.partial.jsonl"
    with gpu_priority("longterm_picks"):
        if not wait_gpu_free(900):
            print("LEARN ALERT: long-term picks: the GPU stayed busy; the cohort will be made next run")
            return
        srv = Ollama(PORT, str(Path.home() / ".ollama" / "models"), 3, out / "ollama.log")
        try:
            llm = OllamaLLM("bonsai-27b:latest", base_url=f"http://127.0.0.1:{PORT}", concurrency=3, num_ctx=8192,
                            num_predict=1000, cache=False, require_gpu=True)

            async def go() -> list[dict[str, Any]]:
                try:
                    return await rate(llm, cs, partial)
                finally:
                    await llm.unload()
            rated = asyncio.run(go())
        finally:
            srv.stop()
    picks = choose(rated)
    if len(picks) != N_PICKS or len({p["ticker"] for p in picks}) != N_PICKS:
        raise ValueError(f"long-term cohort: expected {N_PICKS} distinct rated picks, got {len(picks)}")
    (out / f"ratings_{now.astimezone(NY).strftime('%Y-%m')}.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rated))
    led.append("cohort", month=now.astimezone(NY).strftime("%Y-%m"), made_on=now.astimezone(NY).date().isoformat(),
               made_at=now.isoformat(timespec="seconds"), tickers=[p["ticker"] for p in picks],
               ratings=[p["rating"] for p in picks], scores=[p.get("score") for p in picks], candidates=len(cs),
               rating_counts={str(k): int(v) for k, v in pd.Series([r["rating"] for r in rated]).value_counts().items()})
    partial.unlink(missing_ok=True)  # the cohort is in the ledger: the saved ratings have done their job
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
        report = status(recs)
        print("long-term picks (shadow):", json.dumps(report))
        for key in ("missing_months", "overdue_results", "issues"):
            if report[key]:
                print(f"LEARN ALERT: long-term picks {key}: {report[key]}")
    except Exception as e:  # noqa: BLE001 - a shadow: never fail the events job
        print(f"LEARN ALERT: long-term picks failed: {type(e).__name__}: {e}"[:300])


if __name__ == "__main__":
    main()
