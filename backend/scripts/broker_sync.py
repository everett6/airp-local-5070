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

from app.portfolio.broker import Alpaca, BrokerError, leg_dict, leg_from, opg_open, plan, reconcile
from app.portfolio.guard import HALT, state

REAL_BOOK = ("master+brakes", "master")


def sim_fills(ledger: Path, book: str, decided_at: str) -> dict[str, float]:
    """The simulator's fill prices for the decision made at `decided_at` (the first run after it that filled)."""
    if not ledger.exists():
        return {}
    for line in ledger.read_text().splitlines():
        rec = json.loads(line)
        r = rec.get("books", {}).get(book, {})
        if rec["run_at_utc"] > decided_at and r.get("filled_on"):
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
                client.refresh(leg)
            alerts += reconcile(leg, fills)
        o["legs"] = [leg_dict(x) for x in legs]
    dec = book.get("decided_at")
    if book.get("pending") and dec and dec not in orders and mode != "HALTED":
        acct = client.account()
        pos = client.positions()
        px = client.prices(sorted(set(book["pending"]) | set(pos)))
        legs = plan(dec, book["pending"], float(acct["equity"]), pos, px, reduce_only=mode == "REDUCING")
        orders[dec] = {"book": name, "planned_at": now.isoformat(timespec="seconds"), "equity": float(acct["equity"]),
                       "targets": book["pending"], "legs": [leg_dict(x) for x in legs]}
        print(f"broker: planned {len(legs)} leg(s) for the decision of {dec} on equity {float(acct['equity']):,.0f}")
    for dec, o in orders.items():  # send what may go now
        legs = [leg_from(d) for d in o["legs"]]
        for leg in legs:
            if leg.status != "planned" or mode == "HALTED":
                continue
            if book.get("decided_at") != dec or not book.get("pending"):
                leg.status, leg.note = "skipped", "the simulator already filled or replaced this decision"
                alerts.append(f"broker leg {leg.client_order_id} was never sent before the simulator filled")
                leg.alerted = True
            elif mode == "REDUCING" and leg.side == "buy":
                leg.status, leg.note = "skipped", "REDUCING: sells only"
            elif leg.tif == "gtc" or opg_open(now):
                if dry:
                    print(f"broker (dry): would send {leg.side} {leg.qty} {leg.symbol} {leg.tif}")
                else:
                    client.submit(leg)
                    alerts += reconcile(leg, {})
        o["legs"] = [leg_dict(x) for x in legs]
    path.write_text(json.dumps(orders, indent=1) + "\n")
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
