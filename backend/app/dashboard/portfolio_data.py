"""Data layer for the paper-portfolio viewer (app/dashboard/portfolio.py). No Streamlit imports, so it is testable.

Reads only what the forward runners write, and never writes anything:
  results/forward/allocator/state.json     the books now (cash, positions, pending targets)
  results/forward/allocator/ledger.jsonl   one record per allocator run (equity per book, fills)
  results/forward/<events dir>/ledger.jsonl  the hash-chained event ledger (decisions, missed, outcomes, runs)
The only thing the viewer can change is the kill switch, and only to turn it ON (app/portfolio/guard.py).
Live marks use Yahoo prices (free, may lag a few minutes). A live mark is a view, not a trade: the books only
change when a runner is run by hand.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from app.forward.ledger import Ledger, LedgerError
from app.portfolio.forward import brake_multiplier

BACKEND = Path(__file__).resolve().parents[2]
FWD = BACKEND / "results" / "forward"
ALLOC = FWD / "allocator"
SECTOR_ETF = {"Information Technology": "XLK", "Financials": "XLF", "Health Care": "XLV",
              "Consumer Discretionary": "XLY", "Consumer Staples": "XLP", "Energy": "XLE", "Industrials": "XLI",
              "Materials": "XLB", "Utilities": "XLU", "Real Estate": "XLRE", "Communication Services": "XLC"}
BOOK_LABELS = {"master+brakes": "Main book (with brakes)", "master": "Main book (no brakes)",
               "SPY": "SPY only (benchmark)", "80/20 SPY/BTC": "80/20 SPY/BTC (benchmark)"}
HOLD_DAYS = 5


# ---------- allocator books ----------

def load_state(d: Path = ALLOC) -> dict[str, dict[str, Any]]:
    p = d / "state.json"
    return dict(json.loads(p.read_text())) if p.exists() else {}


def load_runs(d: Path = ALLOC) -> list[dict[str, Any]]:
    p = d / "ledger.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


def equity_history(runs: list[dict[str, Any]]) -> pd.DataFrame:
    """One row per run (indexed by the data date), one column per book: equity marked at that run."""
    rows = {pd.Timestamp(r["data_through"]): {k: v["equity"] for k, v in r["books"].items()} for r in runs}
    return pd.DataFrame.from_dict(rows, orient="index").sort_index()


def trades(runs: list[dict[str, Any]]) -> pd.DataFrame:
    """Every fill, newest first."""
    rows = [{"filled_on": v.get("filled_on"), "book": k, "asset": f["asset"],
             "side": "buy" if f["qty"] > 0 else "sell", "qty": abs(f["qty"]), "price": f["price"],
             "value": abs(f["qty"]) * f["price"]}
            for r in runs for k, v in r["books"].items() for f in v.get("fills", [])]
    cols = ["filled_on", "book", "asset", "side", "qty", "price", "value"]
    return pd.DataFrame(rows, columns=cols).sort_values("filled_on", ascending=False, kind="stable")


def mark(book: dict[str, Any], px: dict[str, float]) -> dict[str, Any]:
    """Value a book at the given prices. Assets without a price are left out of `positions` and listed in
    `unpriced`, and the equity is then None (a partial mark would understate it)."""
    pos, unpriced = [], []
    for a, q in book.get("positions", {}).items():
        if a in px:
            pos.append({"asset": a, "qty": q, "price": px[a], "value": q * px[a]})
        else:
            unpriced.append(a)
    invested = sum(p["value"] for p in pos)
    equity = None if unpriced else book["cash"] + invested
    for p in pos:
        p["weight"] = p["value"] / equity if equity else None
    peak = max(float(book.get("peak") or 0.0), equity or 0.0)
    return {"equity": equity, "cash": book["cash"], "invested": invested, "positions": pos, "unpriced": unpriced,
            "drawdown": (1 - equity / peak) if equity and peak else 0.0,
            "brake": brake_multiplier(equity, peak) if equity else None}


def book_assets(state: dict[str, dict[str, Any]]) -> list[str]:
    return sorted({a for b in state.values() for a in (*b.get("positions", {}), *(b.get("pending") or {}))})


# ---------- event picks (1-week Bonsai shadow book) ----------

def event_dirs(fwd: Path = FWD) -> list[str]:
    """Event ledgers: the real one first, then breadth, then dry runs."""
    found = sorted(p.parent.name for p in fwd.glob("*/ledger.jsonl") if p.parent.name != "allocator")
    order = {"events": 0, "events_breadth": 1}
    return sorted(found, key=lambda n: (order.get(n, 2), n))


def load_events(name: str, fwd: Path = FWD) -> tuple[list[dict[str, Any]], str | None]:
    """(records, error). The chain is verified; a broken chain is reported, never hidden."""
    try:
        return Ledger(fwd / name / "ledger.jsonl").verify(), None
    except LedgerError as e:
        return Ledger(fwd / name / "ledger.jsonl").records(), str(e)


def picks(recs: list[dict[str, Any]]) -> pd.DataFrame:
    """One row per decision or missed release, with its outcome when it has matured."""
    out = {r["accession"]: r for r in recs if r["type"] == "outcome"}
    rows = []
    for r in recs:
        if r["type"] not in ("decision", "missed"):
            continue
        o = out.get(r["accession"], {})
        entry = o.get("entry") or str(r.get("entry_deadline", ""))[:10] or None
        rows.append({"accession": r["accession"], "ticker": r.get("ticker"), "sector": r.get("sector"),
                     "decided": r.get("written_at"), "entry": entry, "source": r.get("source"),
                     "score": r.get("logodds"), "status": "missed" if r["type"] == "missed" else
                     ("closed" if "fwd5" in o else "open"), "reason": r.get("reason"), "fwd5": o.get("fwd5")})
    cols = ["accession", "ticker", "sector", "decided", "entry", "source", "score", "status", "reason", "fwd5"]
    return pd.DataFrame(rows, columns=cols)


def live_excess(p: pd.DataFrame, opens: pd.DataFrame, closes: pd.DataFrame) -> pd.Series:
    """For open picks: the return since the entry open minus the sector ETF's, at the latest price (so far)."""
    res = {}
    for r in p[p["status"] == "open"].itertuples():
        t, etf = str(r.ticker).replace(".", "-"), SECTOR_ETF.get(str(r.sector))
        if not r.entry or etf is None or t not in closes or etf not in closes:
            continue
        day = pd.Timestamp(str(r.entry))
        if day not in opens.index or pd.isna(opens.at[day, t]) or pd.isna(opens.at[day, etf]):
            continue  # not entered yet
        last_t, last_e = closes[t].dropna().iloc[-1], closes[etf].dropna().iloc[-1]
        res[r.accession] = (last_t / opens.at[day, t] - 1) - (last_e / opens.at[day, etf] - 1)
    return pd.Series(res, dtype=float)


def scoreboard(p: pd.DataFrame) -> dict[str, Any]:
    dec = p[p["status"] != "missed"]
    done = dec.dropna(subset=["fwd5"])
    s: dict[str, Any] = {"decisions": len(dec), "missed": int((p["status"] == "missed").sum()),
                         "open": int((p["status"] == "open").sum()), "closed": len(done)}
    for src in ("bonsai", "lite"):
        x = done[done["source"] == src]
        s[f"{src}_n"] = len(x)
        s[f"{src}_ic"] = float(x["score"].rank().corr(x["fwd5"].rank())) if len(x) >= 10 else None
    return s


# ---------- run health ----------

def last_run_times(runs: list[dict[str, Any]], recs: list[dict[str, Any]]) -> dict[str, str | None]:
    ev = [r["as_of"] for r in recs if r["type"] == "run"]
    return {"allocator": runs[-1]["run_at_utc"] if runs else None, "events": ev[-1] if ev else None}


def missed_weekdays(recs: list[dict[str, Any]], today: date | None = None) -> list[date]:
    """Weekdays since the first event run with no event run at all."""
    days = {date.fromisoformat(r["as_of"][:10]) for r in recs if r["type"] == "run"}
    if not days:
        return []
    today = today or datetime.now(UTC).date()
    out, d = [], min(days)
    while d < today:
        if d.weekday() < 5 and d not in days:
            out.append(d)
        d += timedelta(days=1)
    return out


# ---------- prices (network) ----------

def fetch_prices(tickers: list[str], start: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Daily opens and closes from Yahoo since `start`; today's bar is the live, still-forming one."""
    import yfinance as yf  # type: ignore[import-untyped,unused-ignore]
    if not tickers:
        return pd.DataFrame(), pd.DataFrame()
    df = yf.download(sorted(set(tickers)), start=start.isoformat(),
                     end=(datetime.now(UTC).date() + timedelta(days=2)).isoformat(),
                     auto_adjust=True, progress=False, group_by="column", multi_level_index=True, threads=True)
    if df.empty:
        return pd.DataFrame(), pd.DataFrame()
    opens, closes = df["Open"], df["Close"]
    opens.index = pd.DatetimeIndex(opens.index).tz_localize(None).normalize()
    closes.index = opens.index
    return opens, closes


def latest(closes: pd.DataFrame) -> dict[str, float]:
    return {str(c): float(closes[c].dropna().iloc[-1]) for c in closes.columns if closes[c].notna().any()}


# ---------- run log (newest first, like a console's job log) ----------

def run_log(runs: list[dict[str, Any]], recs: list[dict[str, Any]], halt: dict[str, Any] | None = None,
            limit: int = 40) -> list[dict[str, str]]:
    """What each job wrote, newest first: allocator runs (fills, new targets, rejections), event runs, decisions,
    missed releases, outcomes, and the kill switch."""
    log: list[dict[str, str]] = []
    for r in runs:
        dd = r.get("drawdown") or {}
        if dd.get("action"):
            log.append({"at": r["run_at_utc"], "job": "drawdown", "what": f"{dd['book']} {dd['from_peak']:.1%} below "
                        f"its peak: " + ("LIMIT hit: sells only (REDUCING) until --resume" if dd["action"] == "REDUCING" else "alert")})
        for name, b in r["books"].items():
            msg = []
            if b.get("fills"):
                msg.append("filled " + ", ".join(f"{'+' if f['qty'] > 0 else ''}{f['qty']:g} {f['asset']} @ "
                                                  f"{f['price']:,.2f}" for f in b["fills"]))
            if b.get("new_targets"):
                msg.append("targets " + ", ".join(f"{a} {w:.0%}" for a, w in b["new_targets"].items()))
            for k in ("rejected", "rejected_at_fill"):
                if b.get(k):
                    msg.append("REJECTED by mandate: " + "; ".join(b[k]))
            if b.get("held_by_kill_switch"):
                msg.append("orders held: kill switch on")
            log.append({"at": r["run_at_utc"], "job": "allocator", "what": f"{name}: equity "
                        f"${b['equity']:,.0f}" + (" · " + " · ".join(msg) if msg else "")})
    for r in recs:
        t = r["type"]
        if t == "run":
            what = f"event run ({r.get('source', '?')}): {r.get('new', 0)} new release(s)"
            at = r["as_of"]
        elif t == "decision":
            what = f"{r.get('ticker')} score {r.get('logodds', 0):+.2f} ({r.get('source')}), enters {str(r.get('entry_deadline', ''))[:10]}"
            at = r.get("written_at", "")
        elif t == "missed":
            what, at = f"{r.get('ticker')} MISSED: {r.get('reason')}", r.get("written_at", "")
        elif t == "outcome":
            f5 = r.get("fwd5")
            what = f"{r['accession']} closed: " + ("no price" if f5 is None else f"{f5:+.2%} vs sector over 5 days")
            at = r.get("written_at", r.get("as_of", ""))
        else:
            continue
        log.append({"at": at, "job": "events", "what": what})
    if halt:
        log.append({"at": str(halt.get("at", "")), "job": "kill switch", "what": f"ON by {halt.get('by', '?')}: "
                    f"{halt.get('reason', '')}"})
    return sorted(log, key=lambda x: x["at"], reverse=True)[:limit]


def underwater(eq: pd.Series) -> pd.Series:
    """Drawdown below the running peak (0 at a new high, negative below it)."""
    return eq / eq.cummax() - 1
