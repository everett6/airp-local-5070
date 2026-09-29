"""The AI-picks paper sleeve (docs/PLAN_60_V2.md "AI-picks paper sleeve", rules fixed 2026-09-28). UNTESTED: no
research version has passed its backtest; the user chose to trade the picks on paper anyway, capped at 10%.

Each pick is a pair: long the stock, short its sector ETF, the same dollars, entered at the release's entry open and
closed at the open 5 trading days later. Quantities are fixed when the pick is made (from the last close), so the
simulator and the broker mirror trade the same shares; the simulator fills them at the day's open once that bar
exists. State lives in one JSON file; every step is a pure function of (state, decisions, prices, now).
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import pandas as pd

from app.data_ingestion.tickers import trading_symbol
from app.forward.schedule import NY

CAPITAL = 10_000.0
SHARE = 0.10          # of the paper account; the master book's mirror plans on the rest
THRESHOLD = 2.873     # Bonsai 1-week log-odds: the top fifth of the 2025-26 history (factsheet_secchk)
SCORE_NAME = "bonsai_logodds"
SLOTS = 5
HOLD = 5              # trading days
COST = 0.002          # per leg, each way
MAX_DD = 0.25         # no new pairs while the sleeve's drawdown is at least this


def new_state() -> dict[str, Any]:
    return {"cash": CAPITAL, "peak": CAPITAL, "equity": CAPITAL, "threshold": THRESHOLD, "score": SCORE_NAME,
            "pairs": [], "seen": [], "history": []}


def _day_index(days: pd.DatetimeIndex, d: str) -> int | None:
    t = pd.Timestamp(d)
    i = int(days.searchsorted(t))
    return i if i < len(days) and days[i] == t else None


def _px(frame: pd.DataFrame, i: int, t: str) -> float | None:
    if t not in frame.columns:
        return None
    v = frame[t].iloc[i]
    return None if pd.isna(v) or v <= 0 else float(v)


def settle(st: dict[str, Any], opens: pd.DataFrame) -> list[str]:
    """Fill planned entries and due exits whose open is now in the data. Returns notes."""
    days = pd.DatetimeIndex(opens.index)
    notes = []
    for p in st["pairs"]:
        if p["status"] == "planned":
            i = int(days.searchsorted(pd.Timestamp(p["entry_day"])))  # a holiday: the next open, as an opg order
            if i >= len(days):
                continue
            s, e = _px(opens, i, p["ticker"]), _px(opens, i, p["etf"])
            if s is None or e is None:
                p["status"], p["note"] = "skipped", "no open price on the entry day"
                continue
            st["cash"] += -p["qty"] * s * (1 + COST) + p["etf_qty"] * e * (1 - COST)
            p.update(status="open", entry_open=s, etf_entry_open=e, entry_index_day=days[i].date().isoformat())
            notes.append(f"entered {p['ticker']} x{p['qty']} / short {p['etf']} x{p['etf_qty']}")
        if p["status"] == "open":
            k = _day_index(days, p["entry_index_day"])
            assert k is not None
            if k + HOLD >= len(days):
                continue
            j = k + HOLD
            s, e = _px(opens, j, p["ticker"]), _px(opens, j, p["etf"])
            if s is None or e is None:
                continue  # wait for a price
            st["cash"] += p["qty"] * s * (1 - COST) - p["etf_qty"] * e * (1 + COST)
            cost_in = p["qty"] * p["entry_open"] * (1 + COST)
            pnl = (p["qty"] * (s * (1 - COST) - p["entry_open"] * (1 + COST))
                   - p["etf_qty"] * (e * (1 + COST) - p["etf_entry_open"] * (1 - COST)))
            p.update(status="closed", exit_day=days[j].date().isoformat(), exit_open=s, etf_exit_open=e,
                     pnl=round(pnl, 2), ret=round(pnl / cost_in, 6))
            notes.append(f"closed {p['ticker']}: {100 * p['ret']:+.2f}%")
    return notes


def mark(st: dict[str, Any], closes: pd.DataFrame) -> float:
    """Equity at the last close: cash plus longs minus shorts (open pairs at the last close)."""
    eq = st["cash"]
    for p in st["pairs"]:
        if p["status"] == "open":
            s = closes[p["ticker"]].dropna().iloc[-1] if p["ticker"] in closes else p["entry_open"]
            e = closes[p["etf"]].dropna().iloc[-1] if p["etf"] in closes else p["etf_entry_open"]
            eq += p["qty"] * float(s) - p["etf_qty"] * float(e)
    return float(eq)


def pick(st: dict[str, Any], decisions: list[dict[str, Any]], closes: pd.DataFrame, etf_of: dict[str, str],
         now: datetime, mode: str = "ACTIVE") -> list[str]:
    """Plan pairs for new qualifying decisions. Every decision is looked at once (recorded in `seen`)."""
    notes = []
    seen = set(st["seen"])
    eq = mark(st, closes)
    dd = 1 - eq / st["peak"] if st["peak"] > 0 else 0.0
    ds = [r for r in decisions if r.get("type") == "decision"]
    for r in sorted(ds, key=lambda x: (x["entry_deadline"], -float(x.get("logodds") or 0))):
        acc = r["accession"]
        if acc in seen:
            continue
        seen.add(acc)
        if r.get("source") != "bonsai" or float(r.get("logodds") or -99) < st["threshold"]:
            continue
        t, etf = trading_symbol(r["ticker"]), etf_of.get(str(r.get("sector")))
        deadline = datetime.fromisoformat(r["entry_deadline"])
        p: dict[str, Any] = {"accession": acc, "ticker": t, "etf": etf, "logodds": r["logodds"],
                             "entry_day": deadline.astimezone(NY).date().isoformat(),
                             "entry_deadline": r["entry_deadline"], "qty": 0, "etf_qty": 0}
        busy = sum(x["status"] in ("planned", "open") for x in st["pairs"])
        why = ("kill switch" if mode == "HALTED" else "REDUCING: no new pairs" if mode == "REDUCING"
               else "decided after the entry open" if now >= deadline
               else f"sleeve drawdown {100 * dd:.1f}%" if dd >= MAX_DD
               else f"all {SLOTS} slots in use" if busy >= SLOTS
               else "no sector ETF" if etf is None
               else "no recent price" if t not in closes or etf not in closes else "")
        if not why:
            s, e = float(closes[t].dropna().iloc[-1]), float(closes[etf].dropna().iloc[-1])
            slot = eq / SLOTS
            p["qty"] = math.floor(slot / s)
            p["etf_qty"] = math.floor(p["qty"] * s / e)
            if p["qty"] == 0 or p["etf_qty"] == 0:
                why = f"one share costs more than a slot (${slot:,.0f})"
        p["status"], p["note"] = ("skipped", why) if why else ("planned", "")
        st["pairs"].append(p)
        notes.append(f"{'planned' if not why else 'skipped'} {t} (log-odds {r['logodds']}){': ' + why if why else ''}")
    st["seen"] = sorted(seen)
    return notes


def step(st: dict[str, Any], decisions: list[dict[str, Any]], opens: pd.DataFrame, closes: pd.DataFrame,
         etf_of: dict[str, str], now: datetime, mode: str = "ACTIVE") -> list[str]:
    notes = settle(st, opens)
    notes += pick(st, decisions, closes, etf_of, now, mode)
    st["equity"] = round(mark(st, closes), 2)
    st["peak"] = max(st["peak"], st["equity"])
    day = pd.Timestamp(closes.index[-1]).date().isoformat() if len(closes.index) else None
    if day and (not st["history"] or st["history"][-1]["day"] != day):
        st["history"].append({"day": day, "equity": st["equity"]})
    elif day:
        st["history"][-1]["equity"] = st["equity"]
    return notes


def summary(st: dict[str, Any]) -> dict[str, Any]:
    closed = [p for p in st["pairs"] if p["status"] == "closed"]
    return {"equity": st["equity"], "return": round(st["equity"] / CAPITAL - 1, 5),
            "drawdown": round(1 - st["equity"] / st["peak"], 5) if st["peak"] else 0.0,
            "open": sum(p["status"] == "open" for p in st["pairs"]),
            "planned": sum(p["status"] == "planned" for p in st["pairs"]),
            "closed": len(closed), "skipped": sum(p["status"] == "skipped" for p in st["pairs"]),
            "mean_ret": round(sum(p["ret"] for p in closed) / len(closed), 5) if closed else None,
            "hit_rate": round(sum(p["ret"] > 0 for p in closed) / len(closed), 3) if closed else None}
