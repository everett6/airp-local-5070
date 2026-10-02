"""The opportunity funnel of the live event test (docs/PLAN_60_V2.md, "Outside review, second part", new records).

    python scripts/funnel.py            # from the files on disk
    python scripts/funnel.py --sweep    # first ask the SEC again for every release since the start (about 2 minutes),
                                        # so a release no run ever saw is counted too

Every S&P 500 earnings release since the forward test began, the last stage it reached, and why it stopped:

    eligible -> discovered -> downloaded -> extracted -> scored before its open -> evaluated (5-day outcome)
                                                         \\-> selected -> submitted -> filled -> closed   (AI picks)

Without this the results describe only the releases that made it through, and those can be the easy ones: the
table at the end compares the releases scored on time with the ones that were not. Read-only except for its own
output, `results/forward/funnel.json`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import pandas as pd

from app.forward.ledger import Ledger, jsonl_records
from app.forward.schedule import NY, OPEN
from app.portfolio.broker import held_qty

STAGES = ["eligible", "discovered", "downloaded", "extracted", "scored_on_time", "evaluated"]
TRADE = ["scored_on_time", "selected", "submitted", "filled", "closed"]


def _session(accepted_utc: str) -> str:
    t = datetime.fromisoformat(str(accepted_utc)).replace(tzinfo=UTC).astimezone(NY)
    return "weekend" if t.weekday() >= 5 else "before the open" if t.time() < OPEN else "after the open"


def classify(ev: pd.DataFrame, ledger: list[dict[str, Any]], extract: dict[str, dict[str, Any]],
             has_text: set[str], book: dict[str, Any], now: datetime, unseen: pd.DataFrame | None = None
             ) -> list[dict[str, Any]]:
    """One row per release: the stages it reached (in order), where it stopped and why, or what it is waiting for."""
    dec = {r["accession"]: r for r in ledger if r.get("type") == "decision"}
    miss = {r["accession"]: r for r in ledger if r.get("type") == "missed"}
    outc = {r["accession"]: r for r in ledger if r.get("type") == "outcome"}
    pairs = {p["accession"]: p for p in book.get("pairs", [])}
    threshold = float(book.get("threshold", 0.0))
    rows: list[dict[str, Any]] = []
    for r in ev.itertuples():
        acc = str(r.accession)
        row: dict[str, Any] = {"accession": acc, "ticker": r.ticker, "sector": r.sector,
                               "accepted_utc": str(r.accepted_utc), "session": _session(str(r.accepted_utc)),
                               "reached": ["eligible", "discovered"], "stopped": None, "waiting": None, "trade": []}
        rows.append(row)
        url = getattr(r, "ex99_url", None)
        if acc not in has_text:
            row["stopped"] = ("no press release in the filing" if pd.isna(url) or not str(url).strip()
                              else "the press release was not downloaded")
        else:
            row["reached"].append("downloaded")
            x = extract.get(acc)
            if x is None or x.get("model") == "none":
                # no reader: Bonsai-lite still scores the release from the SEC-filed and price parts
                row["note"] = "not read (no GPU: scored by Bonsai-lite)" if x else "not read yet"
            else:
                row["reached"].append("extracted")
        d = dec.get(acc)
        if d is None or not d.get("on_time"):
            if acc in miss:
                row["stopped"] = miss[acc].get("reason", "missed")
            elif row["stopped"] is None:
                row["waiting"] = "its decision (the open is still ahead)"
            if row["stopped"] and row["stopped"].startswith(("no press release", "the press release")) \
                    and acc not in miss:
                row["waiting"], row["stopped"] = f"retried until its open: {row['stopped']}", None
            continue
        row["stopped"] = None
        row["reached"].append("scored_on_time")
        row["logodds"], row["source"] = d.get("logodds"), d.get("source")
        o = outc.get(acc)
        if o is None:
            row["waiting"] = "its 5-day outcome"
        elif o.get("fwd5") is None:
            row["stopped"] = "no outcome (no price at the entry or exit open)"
        else:
            row["reached"].append("evaluated")
            row["fwd5"] = o["fwd5"]
        # the AI-picks branch
        p = pairs.get(acc)
        t = row["trade"]
        if p is None:
            row["trade_stopped"] = ("scored by Bonsai-lite (never traded)" if d.get("source") != "bonsai"
                                    else f"score below the threshold ({threshold})"
                                    if float(d.get("logodds") or -99) < threshold else "not looked at by the sleeve yet")
            continue
        if p["status"] == "skipped" and not p.get("legs"):
            row["trade_stopped"] = f"sleeve skipped it: {p.get('note') or 'no reason recorded'}"
            continue
        t.append("selected")
        ins = [x for x in p.get("legs", []) if "-in-" in x["client_order_id"]]
        if not ins:
            row["trade_stopped" if p["status"] != "planned" else "trade_waiting"] = (
                "orders were never sent" if p["status"] != "planned" else "the order window")
            continue
        t.append("submitted")
        full = {p["ticker"]: p["qty"], p["etf"]: p["etf_qty"]}
        got = {x["asset"]: held_qty(x.get("status", ""), x.get("qty", 0), x.get("filled_qty")) for x in ins}
        if any(x.get("status") == "submitted" for x in ins):
            row["trade_waiting"] = "the entry fills"
            continue
        if any(got.get(a, 0) < q for a, q in full.items()):
            short = ", ".join(f"{x['asset']} {x.get('status')} ({got.get(x['asset'], 0):g} of {x.get('qty', 0):g})"
                              for x in ins if got.get(x["asset"], 0) < x.get("qty", 0))
            row["trade_stopped"] = f"entry not fully filled: {short}"
            continue
        t.append("filled")
        left = (p.get("broker_audit") or {}).get("held")
        if p["status"] == "closed" and left == {}:
            t.append("closed")
            row["pair_ret"] = p.get("ret")
            if p.get("late_exit"):
                row["note"] = "late exit at the broker"
        else:
            row["trade_waiting"] = "its exit" if p["status"] in ("open", "planned") else "the broker exit (late)"
    for r in ([] if unseen is None else unseen.itertuples()):
        rows.append({"accession": str(r.accession), "ticker": r.ticker, "sector": r.sector,
                     "accepted_utc": str(r.accepted_utc), "session": _session(str(r.accepted_utc)),
                     "reached": ["eligible"], "stopped": "no run ever saw it (PC off, or the filing list was unreadable)",
                     "waiting": None, "trade": []})
    return rows


def summarise(rows: list[dict[str, Any]], swept: bool) -> dict[str, Any]:
    counts = {s: sum(s in r["reached"] for r in rows) for s in STAGES}
    trade = {s: sum(s in r["trade"] for r in rows) for s in TRADE[1:]}
    stopped = Counter((r["reached"][-1], r["stopped"]) for r in rows if r["stopped"])
    waiting = Counter((r["reached"][-1], r["waiting"]) for r in rows if r["waiting"])
    tstop = Counter(((r["trade"] or ["scored_on_time"])[-1], r["trade_stopped"]) for r in rows if r.get("trade_stopped"))
    twait = Counter(((r["trade"] or ["scored_on_time"])[-1], r["trade_waiting"]) for r in rows if r.get("trade_waiting"))

    def table(c: Counter[tuple[str, str]]) -> list[dict[str, Any]]:
        return [{"after": a, "why": w, "releases": n} for (a, w), n in sorted(c.items(), key=lambda kv: -kv[1])]

    def split(key: str) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for r in rows:
            g = out.setdefault(str(r[key]), {"releases": 0, "scored_on_time": 0})
            g["releases"] += 1
            g["scored_on_time"] += "scored_on_time" in r["reached"]
        return dict(sorted(out.items()))
    return {"releases": len(rows), "eligible_is": "asked again at the SEC" if swept else "the releases the runs found",
            "stages": counts, "trade_stages": {"scored_on_time": counts["scored_on_time"], **trade},
            "stopped": table(stopped), "waiting": table(waiting),
            "trade_stopped": table(tstop), "trade_waiting": table(twait),
            "who_gets_scored": {"by_session": split("session"), "by_sector": split("sector")}}


def load(events_dir: Path, picks_dir: Path) -> tuple[pd.DataFrame, list[dict[str, Any]], dict[str, dict[str, Any]],
                                                    set[str], dict[str, Any]]:
    from extract_events import text_path
    ev = pd.read_csv(events_dir / "events.csv").drop_duplicates("accession")
    ledger = Ledger(events_dir / "ledger.jsonl").records()
    extract = {x["accession"]: x for x in jsonl_records(events_dir / "extract.jsonl")}
    has_text = {a for a in ev["accession"] if text_path(str(a)).exists()}
    book = json.loads((picks_dir / "book.json").read_text()) if (picks_dir / "book.json").exists() else {}
    return ev, ledger, extract, has_text, book


def sweep(ev: pd.DataFrame, start: date, now: datetime) -> pd.DataFrame:
    """Every release the SEC lists for the members since `start` that no run recorded."""
    from forward_events import discover
    found = asyncio.run(discover(start, now))
    return found[~found["accession"].isin(set(ev["accession"]))]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="results/forward/events")
    ap.add_argument("--picks", default="results/forward/ai_picks")
    ap.add_argument("--out", default="results/forward/funnel.json")
    ap.add_argument("--start", default="2026-09-30", help="first filing date of the forward test")
    ap.add_argument("--sweep", action="store_true")
    a = ap.parse_args()
    d = BACKEND / a.dir
    if not (d / "events.csv").exists() or not (d / "ledger.jsonl").exists():
        print("funnel: no live releases yet")
        return
    now = datetime.now(UTC)
    ev, ledger, extract, has_text, book = load(d, BACKEND / a.picks)
    ev = ev[ev["filed"] >= a.start]
    unseen = None
    if a.sweep:
        try:
            unseen = sweep(ev, date.fromisoformat(a.start), now)
        except Exception as e:  # noqa: BLE001 - the sweep is a network call; the funnel from disk still stands
            print(f"funnel: the SEC sweep failed ({type(e).__name__}: {e}); counting only what the runs found")
    rows = classify(ev, ledger, extract, has_text, book, now, unseen)
    rep = {"at": now.isoformat(timespec="seconds"), **summarise(rows, unseen is not None), "rows": rows}
    out = BACKEND / a.out
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(rep, indent=1) + "\n")
    tmp.replace(out)
    s = rep["stages"]
    print("funnel: " + " -> ".join(f"{k.replace('_', ' ')} {s[k]}" for k in STAGES))
    print("  AI picks: " + " -> ".join(f"{k.replace('_', ' ')} {v}" for k, v in rep["trade_stages"].items()))
    for name in ("stopped", "waiting", "trade_stopped", "trade_waiting"):
        for x in rep[name]:
            print(f"  {name.replace('_', ' ')} after {x['after'].replace('_', ' ')}: {x['releases']} x {x['why']}")
    if unseen is not None and len(unseen):
        print(f"LEARN ALERT: funnel: {len(unseen)} release(s) since {a.start} that no run recorded "
              f"({', '.join(map(str, unseen['ticker'][:8]))})")


if __name__ == "__main__":
    main()
