"""Mirror the forward book's pending decision to the Alpaca PAPER account and reconcile fills (app/portfolio/broker.py).

    python scripts/broker_sync.py                   # real book: plan / submit / refresh / reconcile
    python scripts/broker_sync.py --dry --dir results/forward/allocator_autodry --out results/forward/broker_autodry

Run by autorun.py after each allocator run (crypto legs go at once) and before each event run (the 08:45 ET run is
inside Alpaca's market-on-open window, so SPY legs go then). Idempotent: a decision is planned once and every leg has
a fixed client order id. Without Alpaca paper keys in backend/.env it prints one line and exits 0. It never fails the
job: problems print as "BROKER ALERT: ..." lines, which autorun turns into alerts.
`--dry` reads the account and prices but sends and cancels nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import httpx

from app.forward.ledger import jsonl_records, write_atomic
from app.portfolio.broker import (
    CRYPTO,
    STOCKS,
    Alpaca,
    BrokerError,
    leg_dict,
    leg_from,
    opg_open,
    plan,
    reconcile,
)
from app.portfolio.forward import AGGRESSIVE
from app.portfolio.guard import HALT, state
from app.portfolio.sleeve import SHARE as SLEEVE_SHARE

# The paper account mirrors the user's aggressive book when it exists (user, 2026-09-30), else the frozen book.
REAL_BOOK = (AGGRESSIVE, "master+brakes", "master")
STOCK_MARGIN, BROKER_ROOM = 0.5, 0.98  # the account lends 2x overnight on stocks and nothing against crypto
MAX_AGE_DAYS = 3  # an older decision fills in the simulator at a past open: mirroring it now would only make a gap


def broker_scale(targets: dict[str, float]) -> float:
    """How much of these weights the account can hold overnight: stocks need half their value in equity, crypto all
    of it. 1.0 for an unleveraged book; about 0.66 for the 2.5x book at full size (so roughly 1.6x at the broker)."""
    need = sum(w * (1.0 if a in CRYPTO else STOCK_MARGIN) for a, w in targets.items() if w > 0)
    return min(1.0, BROKER_ROOM / need) if need > 0 else 1.0


def sim_fills(ledger: Path, book: str, decided_at: str) -> dict[str, float]:
    """The simulator's fill prices for the decision made at `decided_at` (the first run after it that filled)."""
    for rec in jsonl_records(ledger):
        r = rec.get("books", {}).get(book, {})
        if rec.get("run_at_utc", "") > decided_at and r.get("filled_on"):
            return {f["asset"]: float(f["price"]) for f in r.get("fills", [])}
    return {}


def sync(client: Alpaca, alloc: Path, out: Path, now: datetime, dry: bool, halt_path: Path = HALT) -> list[str]:
    alerts: list[str] = []
    st = json.loads((alloc / "state.json").read_text()) if (alloc / "state.json").exists() else {}
    name = next((n for n in REAL_BOOK if n in st), None)
    if name is None:
        print("broker: no mirrored book in the allocator state yet")
        return alerts
    book = st[name]
    out.mkdir(parents=True, exist_ok=True)
    path = out / "orders.json"
    orders: dict[str, Any] = json.loads(path.read_text()) if path.exists() else {}
    mode = state(halt_path)
    if mode == "HALTED":
        if not dry:
            client.cancel_all()
        for o in orders.values():
            for d in o["legs"]:
                if d["status"] == "planned":
                    d["status"], d["note"] = "skipped", "kill switch"
        print("broker: kill switch on; open orders cancelled, nothing sent")
    for dec, o in orders.items():  # refresh what was sent, then compare with the simulator
        legs = [leg_from(d) for d in o["legs"]]
        fills = sim_fills(alloc / "ledger.jsonl", o["book"], dec)
        for leg in legs:
            if leg.status == "submitted" and not dry:
                try:
                    client.refresh(leg)
                except (BrokerError, httpx.HTTPError) as e:  # one unreadable order must not block the others
                    alerts.append(f"broker order {leg.client_order_id} refresh uncertain: {type(e).__name__}: {e}"[:200])
            alerts += reconcile(leg, fills)
        o["legs"] = [leg_dict(x) for x in legs]
    dec = book.get("decided_at")
    stale = bool(dec) and (now - datetime.fromisoformat(dec)).days > MAX_AGE_DAYS
    if book.get("pending") and dec and dec not in orders and stale:
        orders[dec] = {"book": name, "planned_at": now.isoformat(timespec="seconds"), "legs": [],
                       "skipped": f"decision older than {MAX_AGE_DAYS} days (the simulator fills it at a past open)"}
        print(f"broker: skipped the decision of {dec}: older than {MAX_AGE_DAYS} days")
    elif book.get("pending") and dec and dec not in orders and mode != "HALTED":
        acct = client.account()
        pos = client.positions()
        px = client.prices(sorted(set(book["pending"]) | set(pos)))
        equity = float(acct["equity"]) * (1 - SLEEVE_SHARE)  # the AI-picks sleeve trades the rest (sleeve.py)
        scale = broker_scale(book["pending"])
        want = {a: w * scale for a, w in book["pending"].items()}
        legs = plan(dec, want, equity, pos, px, reduce_only=mode == "REDUCING")
        orders[dec] = {"book": name, "planned_at": now.isoformat(timespec="seconds"), "equity": equity,
                       "targets": book["pending"], "broker_scale": round(scale, 4),
                       "broker_gross": round(sum(want.values()), 3), "legs": [leg_dict(x) for x in legs]}
        print(f"broker: planned {len(legs)} leg(s) for the decision of {dec} on equity {equity:,.0f}"
              + (f" at {scale:.0%} of the {name} weights (gross {sum(want.values()):.2f}x; the account's margin limit)"
                 if scale < 1 else ""))
    wiped = f"wiped-{name}"  # PLAN_60_V2 "Rule changes after the records were checked", 4
    if book.get("wiped") and wiped not in orders and mode != "HALTED":
        # the simulator closed this book for good and will decide nothing more: sell what it holds at the broker
        # (the book's own assets only; the AI-picks sleeve's pairs are not touched) and buy nothing back
        pos = client.positions()
        px = client.prices(sorted(a for a in pos if a in CRYPTO or a in STOCKS))
        legs = plan(wiped, {}, 0.0, pos, px)
        orders[wiped] = {"book": name, "planned_at": now.isoformat(timespec="seconds"), "equity": 0.0, "targets": {},
                         "flatten": True, "legs": [leg_dict(x) for x in legs]}
        alerts.append(f"the {name} book was wiped out in the simulator: {len(legs)} sell order(s) planned to close "
                      "its holdings at the broker; nothing is bought back")
    for dec, o in orders.items():  # send what may go now
        legs = [leg_from(d) for d in o["legs"]]
        for leg in legs:
            if leg.status != "planned" or mode == "HALTED":
                continue
            if not o.get("flatten") and (book.get("decided_at") != dec or not book.get("pending")):
                leg.status, leg.note = "skipped", "the simulator already filled or replaced this decision"
                alerts.append(f"broker leg {leg.client_order_id} was never sent before the simulator filled")
                leg.alerted = True
            elif mode == "REDUCING" and leg.side == "buy":
                leg.status, leg.note = "skipped", "REDUCING: sells only"
            elif leg.tif == "gtc" or opg_open(now):
                if dry:
                    print(f"broker (dry): would send {leg.side} {leg.qty} {leg.symbol} {leg.tif}")
                else:
                    try:
                        client.submit(leg)
                    except (BrokerError, httpx.HTTPError) as e:
                        leg.status = "submitted"  # it may have reached the broker: looked up by its id next run
                        leg.note = f"submission uncertain: {type(e).__name__}: {e}"[:160]
                        alerts.append(f"broker leg {leg.client_order_id} {leg.note}")
                    alerts += reconcile(leg, {})
        o["legs"] = [leg_dict(x) for x in legs]
    write_atomic(path, json.dumps(orders, indent=1) + "\n")
    for dec in sorted(orders)[-1:]:  # the latest decision's legs
        for d in orders[dec]["legs"]:
            gap = "" if d["gap"] is None else f" gap {100 * d['gap']:+.2f}%"
            print(f"  {dec[:16]} {d['side']:4} {d['qty']:>12} {d['symbol']:8} {d['status']}{gap}")
    return alerts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="results/forward/allocator", help="the allocator folder whose book is mirrored")
    ap.add_argument("--out", default="results/forward/broker")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    client = Alpaca.from_env()
    if client is None:
        print("broker: skipped (no Alpaca paper keys in backend/.env)")
        return
    alloc = BACKEND / a.dir
    halt_path = alloc / "HALT" if a.dry else HALT
    try:
        alerts = sync(client, alloc, BACKEND / a.out, datetime.now(UTC), a.dry, halt_path)
    except (BrokerError, httpx.HTTPError, OSError, ValueError, KeyError) as e:  # a mirror: never fail the book's job
        alerts = [f"broker sync failed: {type(e).__name__}: {e}"[:300]]
    for x in alerts:
        print("BROKER ALERT:", x)


if __name__ == "__main__":
    main()
