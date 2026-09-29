"""The AI-picks paper sleeve, UNTESTED (app/portfolio/sleeve.py; rules in docs/PLAN_60_V2.md "AI-picks paper sleeve").

    python scripts/ai_picks.py                     # step the sleeve on the forward event ledger, mirror to Alpaca paper
    python scripts/ai_picks.py --status
    python scripts/ai_picks.py --dry --events results/forward/events_autodry --dir results/forward/ai_picks_autodry

Run by autorun.py after each event run. Pairs (long stock, short sector ETF) are entered with market-on-open orders by
the 08:45 ET run and closed the same way 5 trading days later. Idempotent: every leg has a fixed client order id.
`--dry` reads the account but sends nothing. Problems print as "BROKER ALERT: ..." lines (autorun alerts on them);
this step never fails the job.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import httpx
import pandas as pd
from event_eval import SECTOR_ETF

from app.forward.ledger import Ledger
from app.portfolio import sleeve
from app.portfolio.broker import Alpaca, BrokerError, Leg, leg_dict, leg_from, opg_open, reconcile
from app.portfolio.guard import HALT, state
from app.sandbox.events import Prices


def leg_id(acc: str, when: str, part: str) -> str:
    return f"airp-pk-{re.sub(r'[^0-9]', '', acc)}-{when}-{part}"


def due_legs(p: dict[str, Any], days: pd.DatetimeIndex, now: datetime) -> list[Leg]:
    """The legs that should be sent now for this pair (none if they were sent already or it's not their time)."""
    if not opg_open(now):
        return []
    have = {d["client_order_id"] for d in p.get("legs", [])}
    if p["status"] == "planned" and now < datetime.fromisoformat(p["entry_deadline"]):
        legs = [Leg(p["ticker"], p["ticker"], "buy", p["qty"], "opg", leg_id(p["accession"], "in", "s"), 0.0),
                Leg(p["etf"], p["etf"], "sell", p["etf_qty"], "opg", leg_id(p["accession"], "in", "e"), 0.0)]
    elif p["status"] == "open":
        i = int(days.searchsorted(pd.Timestamp(p["entry_index_day"])))
        if len(days) - 1 - i < sleeve.HOLD - 1:  # the exit open is not the next one yet
            return []
        legs = [Leg(p["ticker"], p["ticker"], "sell", p["qty"], "opg", leg_id(p["accession"], "out", "s"), 0.0),
                Leg(p["etf"], p["etf"], "buy", p["etf_qty"], "opg", leg_id(p["accession"], "out", "e"), 0.0)]
    else:
        return []
    return [x for x in legs if x.client_order_id not in have]


def audit_pair(p: dict[str, Any], legs: list[Leg]) -> list[str]:
    """Record both-leg status and fill slippage for one paper pair; alert on a broken hedge."""
    if p["status"] == "skipped":
        return []
    flags: list[str] = []
    by_phase: dict[str, list[Leg]] = {phase: [x for x in legs if f"-{phase}-" in x.client_order_id]
                                            for phase in ("in", "out")}
    for phase, required in (("in", p["status"] in ("open", "closed")),
                            ("out", p["status"] == "closed")):
        pair = by_phase[phase]
        assets = {x.asset for x in pair}
        if required or pair:
            missing = {p["ticker"], p["etf"]} - assets
            if missing:
                flags.append(f"{phase} hedge missing {', '.join(sorted(missing))}")
            bad = [x.asset for x in pair if x.status in ("rejected", "canceled", "expired")]
            if bad:
                flags.append(f"{phase} hedge rejected/canceled: {', '.join(sorted(bad))}")
            if required and len([x for x in pair if x.status == "filled"]) < 2:
                flags.append(f"{phase} hedge not fully filled")
    current = set(flags)
    old = set(p.get("audit_flags", []))
    p["audit_flags"] = sorted(current)
    p["broker_audit"] = {"entry": {x.asset: x.status for x in by_phase["in"]},
                         "exit": {x.asset: x.status for x in by_phase["out"]}}
    if p["status"] == "closed" and all(len(by_phase[k]) == 2 and
                                        all(x.status == "filled" and x.filled_price for x in by_phase[k])
                                        for k in ("in", "out")):
        entry = {x.asset: float(x.filled_price) for x in by_phase["in"] if x.filled_price}
        exit_ = {x.asset: float(x.filled_price) for x in by_phase["out"] if x.filled_price}
        q, h = p["qty"], p["etf_qty"]
        broker_gross = q * (exit_[p["ticker"]] - entry[p["ticker"]]) - h * (exit_[p["etf"]] - entry[p["etf"]])
        sim_cost = sleeve.COST * (q * (p["entry_open"] + p["exit_open"]) +
                                  h * (p["etf_entry_open"] + p["etf_exit_open"]))
        sim_gross = p["pnl"] + sim_cost
        p["broker_audit"].update(broker_gross_pnl=round(broker_gross, 2),
                                 simulator_net_pnl=p["pnl"], assumed_sim_cost=round(sim_cost, 2),
                                 fill_slippage=round(broker_gross - sim_gross, 2))
    return [f"ai picks {p['ticker']} {flag}" for flag in sorted(current - old)]


def mirror(st: dict[str, Any], client: Alpaca, days: pd.DatetimeIndex, now: datetime, dry: bool, mode: str
           ) -> list[str]:
    alerts: list[str] = []
    for p in st["pairs"]:
        legs = [leg_from(d) for d in p.get("legs", [])]
        sims = {"in": {p["ticker"]: p.get("entry_open"), p["etf"]: p.get("etf_entry_open")},
                "out": {p["ticker"]: p.get("exit_open"), p["etf"]: p.get("etf_exit_open")}}
        for leg in legs:
            if leg.status == "submitted" and not dry:
                try:
                    client.refresh(leg)
                except (BrokerError, httpx.HTTPError) as e:
                    alerts.append(f"ai picks {p['ticker']} order refresh uncertain: {type(e).__name__}: {e}")
            when = "in" if "-in-" in leg.client_order_id else "out"
            alerts += reconcile(leg, {k: v for k, v in sims[when].items() if v})
        if mode != "HALTED":
            for leg in due_legs(p, days, now):
                if mode == "REDUCING" and "-in-" in leg.client_order_id:
                    continue
                if dry:
                    print(f"ai picks (dry): would send {leg.side} {leg.qty} {leg.symbol} opg")
                    leg.status = "dry"
                else:
                    try:
                        client.submit(leg)
                    except (BrokerError, httpx.HTTPError) as e:
                        leg.status = "submitted"  # may have reached the broker; refresh by id next run
                        leg.note = f"submission uncertain: {type(e).__name__}: {e}"[:160]
                        alerts.append(f"ai picks {p['ticker']} {leg.asset} {leg.note}")
                    alerts += reconcile(leg, {})
                legs.append(leg)
        if not dry and p["status"] in ("open", "closed") and not any("-in-" in x.client_order_id for x in legs) \
                and not p.get("unsent_alerted"):
            alerts.append(f"ai picks: {p['ticker']} entered in the simulator but its orders were never sent")
            p["unsent_alerted"] = True
        p["legs"] = [leg_dict(x) for x in legs if x.status != "dry"]
        if not dry:
            alerts += audit_pair(p, legs)
    return alerts


def run(events: Path, out: Path, now: datetime, dry: bool, client: Alpaca | None, halt_path: Path) -> list[str]:
    path = out / "book.json"
    st = json.loads(path.read_text()) if path.exists() else sleeve.new_state()
    if not (events / "ledger.jsonl").exists() or not (events / "prices.parquet").exists():
        print("ai picks: no event ledger or prices yet")
        return []
    recs = Ledger(events / "ledger.jsonl").verify()
    p = Prices.from_long(pd.read_parquet(events / "prices.parquet"))
    mode = state(halt_path)
    for n in sleeve.step(st, recs, p.open, p.close, SECTOR_ETF, now, mode):
        print("ai picks:", n)
    alerts = mirror(st, client, pd.DatetimeIndex(p.open.index), now, dry, mode) if client else []
    st["broker_audit_status"] = "dry" if dry else "no_keys" if client is None else "checked"
    out.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1) + "\n")
    tmp.replace(path)
    print("ai picks (UNTESTED sleeve):", json.dumps(sleeve.summary(st)))
    return alerts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--events", default="results/forward/events")
    ap.add_argument("--dir", default="results/forward/ai_picks")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    out = BACKEND / a.dir
    if a.status:
        st = json.loads((out / "book.json").read_text()) if (out / "book.json").exists() else sleeve.new_state()
        print(json.dumps(sleeve.summary(st), indent=1))
        return
    try:
        client = Alpaca.from_env()
        if client is None:
            print("ai picks: broker mirror skipped (no Alpaca paper keys in backend/.env)")
        halt_path = out / "HALT" if a.dry else HALT
        alerts = run(BACKEND / a.events, out, datetime.now(UTC), a.dry, client, halt_path)
    except (BrokerError, httpx.HTTPError, OSError, ValueError, KeyError) as e:
        alerts = [f"ai picks failed: {type(e).__name__}: {e}"[:300]]
    for x in alerts:
        print("BROKER ALERT:", x)
    if alerts:
        raise SystemExit(1)  # never mark a broken paper hedge as a clean event run


if __name__ == "__main__":
    main()
