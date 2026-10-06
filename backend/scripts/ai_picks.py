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
from app.portfolio.broker import (
    STOCK_TIF,
    UNFILLED,
    Alpaca,
    BrokerError,
    Leg,
    held,
    held_qty,
    leg_dict,
    leg_from,
    opg_open,
    reconcile,
    symbol,
)
from app.portfolio.guard import HALT, state
from app.sandbox.events import Prices


def leg_id(acc: str, when: str, part: str) -> str:
    return f"airp-pk-{re.sub(r'[^0-9]', '', acc)}-{when}-{part}"


MAX_EXIT_TRIES = 5  # an exit that five orders could not complete is left to the user (alerted)


def _phase(d: dict[str, Any]) -> str:
    return "in" if "-in-" in d["client_order_id"] else "out"


def _held(p: dict[str, Any], d: dict[str, Any]) -> float:
    """What this order put into (or took out of) the account, in shares."""
    full = p["qty"] if d.get("asset") == p["ticker"] else p["etf_qty"]
    return held_qty(d.get("status", ""), d.get("qty", full), d.get("filled_qty"))


def exposure(p: dict[str, Any]) -> dict[str, float]:
    """Per asset, the shares this pair still has at the broker: entry fills minus exit fills. A part-filled order
    counts for what it filled, whatever its final status."""
    out: dict[str, float] = {}
    for d in p.get("legs", []):
        q = _held(p, d)
        if q:
            a = d.get("asset", "")
            out[a] = out.get(a, 0.0) + (q if _phase(d) == "in" else -q)
    return {a: q for a, q in out.items() if q > 1e-9}


def entered(p: dict[str, Any]) -> set[str]:
    """The pair's assets with any entry fill at the broker."""
    return {d.get("asset", "") for d in p.get("legs", []) if _phase(d) == "in" and _held(p, d) > 0}


def exit_due(p: dict[str, Any], days: pd.DatetimeIndex) -> bool:
    """The pair's scheduled exit open is the next one, or is already behind it."""
    if p["status"] == "open":
        i = int(days.searchsorted(pd.Timestamp(p["entry_index_day"])))
        return len(days) - 1 - i >= sleeve.HOLD - 1
    return p["status"] in ("closed", "skipped")  # closed in the simulator, or never opened there after its entry filled


def risk_blocked(leg: dict[str, Any]) -> bool:
    """A local pretrade refusal sent no POST and can reuse its original client ID."""
    return leg.get("status") == "rejected" and not leg.get("order_id") and str(leg.get("note", "")).startswith("ACCOUNT RISK:")


def due_legs(p: dict[str, Any], days: pd.DatetimeIndex, now: datetime) -> list[Leg]:
    """The legs that should be sent now for this pair (none if they were sent already or it's not their time).

    Entries follow the simulator's plan. Exits follow the broker: whatever the pair still holds there is closed
    from its scheduled exit on, even if the simulator closed the pair runs ago (a missed run) or never opened it,
    and an exit order that ended short is followed by a new one for the rest (docs/PLAN_60_V2.md, "Outside
    review, second part", rule 1)."""
    if not opg_open(now):
        return []
    have = {d["client_order_id"] for d in p.get("legs", []) if not risk_blocked(d)}
    if p["status"] == "planned" and now < datetime.fromisoformat(p["entry_deadline"]):
        legs = [Leg(p["ticker"], symbol(p["ticker"]), "buy", p["qty"], STOCK_TIF, leg_id(p["accession"], "in", "s"), 0.0),
                Leg(p["etf"], symbol(p["etf"]), "sell", p["etf_qty"], STOCK_TIF, leg_id(p["accession"], "in", "e"), 0.0)]
        return [x for x in legs if x.client_order_id not in have]
    if p["status"] == "planned" or not exit_due(p, days):
        return []
    legs = []
    # close only what the broker holds: an entry leg that never filled (expired, rejected) has no exit leg,
    # or the "exit" would open a new position and eat into another pair's hedge
    for asset, part, side in ((p["ticker"], "s", "sell"), (p["etf"], "e", "buy")):
        left = exposure(p).get(asset, 0.0)
        outs = [d for d in p.get("legs", []) if _phase(d) == "out" and d.get("asset") == asset]
        if left <= 0 or any(d.get("status") in ("submitted", "planned") for d in outs) or sum(not risk_blocked(d) for d in outs) >= MAX_EXIT_TRIES:
            continue  # nothing held, an exit order is still working, or enough tries
        if outs and risk_blocked(outs[-1]):
            leg = leg_from(outs[-1])
            leg.status, leg.note = "planned", ""
            legs.append(leg)
            continue
        cid = leg_id(p["accession"], "out", part) + (f"-r{len(outs) + 1}" if outs else "")
        legs.append(Leg(asset, symbol(asset), side, int(left) if float(left).is_integer() else left, STOCK_TIF,
                        cid, 0.0))
    return [x for x in legs if x.client_order_id not in have]


def late_exit(p: dict[str, Any], leg: Leg) -> bool:
    """An exit order sent after the pair's scheduled exit open: the simulator has already closed (or skipped) the
    pair, or this order replaces one that ended short."""
    return "-out-" in leg.client_order_id and (p["status"] != "open" or "-r" in leg.client_order_id.split("-out-")[1])


def audit_pair(p: dict[str, Any], legs: list[Leg]) -> list[str]:
    """Record both-leg status and fill slippage for one paper pair; alert on a broken hedge."""
    if p["status"] == "skipped" and not legs:
        return []
    flags: list[str] = []
    by_phase: dict[str, list[Leg]] = {phase: [x for x in legs if f"-{phase}-" in x.client_order_id]
                                            for phase in ("in", "out")}
    left = exposure({**p, "legs": [leg_dict(x) for x in legs]})
    pair = by_phase["in"]
    if p["status"] in ("open", "closed", "skipped") or pair:
        missing = {p["ticker"], p["etf"]} - {x.asset for x in pair}
        if missing and p["status"] != "skipped":
            flags.append(f"in hedge missing {', '.join(sorted(missing))}")
        bad = [x.asset for x in pair if x.status in UNFILLED]
        if bad:
            flags.append(f"in hedge rejected/canceled: {', '.join(sorted(bad))}")
        part = [f"{x.asset} {held(x):g} of {x.qty:g}" for x in pair if x.status in UNFILLED and held(x) > 0]
        if part:
            flags.append(f"in hedge part-filled: {', '.join(sorted(part))}")
        if p["status"] in ("open", "closed") and len([x for x in pair if x.status == "filled"]) < 2:
            flags.append("in hedge not fully filled")
        if p["status"] == "skipped" and left:
            flags.append("entry filled at the broker but the simulator skipped the pair")
    pair = by_phase["out"]
    if p["status"] in ("closed", "skipped") or pair:
        missing = set(left) - {x.asset for x in pair}
        if missing and p["status"] in ("closed", "skipped"):
            flags.append(f"out hedge missing {', '.join(sorted(missing))}")
        short = {x.asset for x in pair if x.status in UNFILLED} & set(left)  # cleared once a later order closes it
        if short:
            flags.append(f"out hedge rejected/canceled: {', '.join(sorted(short))}")
        if p["status"] in ("closed", "skipped") and left:
            flags.append("out hedge not fully filled")
        stuck = [a for a in left if len([x for x in pair if x.asset == a and not risk_blocked(leg_dict(x))]) >= MAX_EXIT_TRIES]
        if stuck:
            flags.append(f"exit gave up after {MAX_EXIT_TRIES} orders, still held: {', '.join(sorted(stuck))}")
    if p.get("late_exit"):
        flags.append("late exit")
    current = set(flags)
    old = set(p.get("audit_flags", []))
    p["audit_flags"] = sorted(current)
    p["broker_audit"] = {"entry": {x.asset: x.status for x in by_phase["in"]},
                         "exit": {x.asset: x.status for x in by_phase["out"]},
                         "held": {a: (int(q) if float(q).is_integer() else q) for a, q in sorted(left.items())}}
    fills = [x for x in legs if held(x) > 0]
    if p["status"] in ("closed", "skipped") and fills and not left and all(x.filled_price for x in fills):
        # what the broker's fills made, all of them: buys cost, sells pay
        broker_gross = sum((1 if x.side == "sell" else -1) * held(x) * float(x.filled_price or 0.0) for x in fills)
        p["broker_audit"].update(broker_gross_pnl=round(broker_gross, 2), late=bool(p.get("late_exit")))
        ins = {x.asset: held(x) for x in by_phase["in"]}
        if p["status"] == "closed" and ins == {p["ticker"]: float(p["qty"]), p["etf"]: float(p["etf_qty"])}:
            q, h = p["qty"], p["etf_qty"]  # the broker traded the simulator's shares: the two can be compared
            sim_cost = sleeve.COST * (q * (p["entry_open"] + p["exit_open"]) +
                                      h * (p["etf_entry_open"] + p["etf_exit_open"]))
            sim_gross = p["pnl"] + sim_cost
            p["broker_audit"].update(simulator_net_pnl=p["pnl"], assumed_sim_cost=round(sim_cost, 2),
                                     fill_slippage=round(broker_gross - sim_gross, 2))
    return [f"ai picks {p['ticker']} {flag}" for flag in sorted(current - old)]


def mirror(st: dict[str, Any], client: Alpaca, days: pd.DatetimeIndex, now: datetime, dry: bool, mode: str,
           references: dict[str, float] | None = None, allowed_stocks: set[str] | None = None) -> list[str]:
    alerts: list[str] = []
    for p in st["pairs"]:
        legs = [leg_from(d) for d in p.get("legs", [])]
        sims = {"in": {p["ticker"]: p.get("entry_open"), p["etf"]: p.get("etf_entry_open")},
                "out": {p["ticker"]: p.get("exit_open"), p["etf"]: p.get("etf_exit_open")}}
        for leg in legs:
            if leg.status == "submitted" and not dry:
                try:
                    if allowed_stocks is not None and p["ticker"] not in allowed_stocks and "-in-" in leg.client_order_id:
                        client.cancel_leg(leg)
                    client.refresh(leg)
                except (BrokerError, httpx.HTTPError) as e:
                    alerts.append(f"ai picks {p['ticker']} order refresh uncertain: {type(e).__name__}: {e}")
            when = "in" if "-in-" in leg.client_order_id else "out"
            alerts += reconcile(leg, {k: v for k, v in sims[when].items() if v})
        p["legs"] = [leg_dict(x) for x in legs]  # due_legs reads the refreshed entry fills
        restricted_wait = allowed_stocks is not None and p["ticker"] not in allowed_stocks and any(
            leg.status == "submitted" and "-in-" in leg.client_order_id for leg in legs)
        if restricted_wait:
            alerts.append(f"ai picks {p['ticker']}: restricted entry cancellation still pending; waiting before exits")
        if mode != "HALTED" and not restricted_wait:
            for leg in due_legs(p, days, now):
                # The reference is the latest completed close available to this run,
                # never a future entry/exit open. Missing prices still fail the gate.
                leg.ref_price = (references or {}).get(leg.asset, 0.0)
                if "-in-" in leg.client_order_id and allowed_stocks is not None and p["ticker"] not in allowed_stocks:
                    continue
                if mode == "REDUCING" and "-in-" in leg.client_order_id:
                    continue
                if late_exit(p, leg) and not p.get("late_exit"):
                    p["late_exit"] = now.isoformat(timespec="seconds")  # audit_pair raises the alert, once
                if dry:
                    print(f"ai picks (dry): would send {leg.side} {leg.qty} {leg.symbol} at the open")
                    leg.status = "dry"
                else:
                    try:
                        client.submit(leg)
                    except (BrokerError, httpx.HTTPError) as e:
                        leg.status = "submitted"  # may have reached the broker; refresh by id next run
                        leg.note = f"submission uncertain: {type(e).__name__}: {e}"[:160]
                        alerts.append(f"ai picks {p['ticker']} {leg.asset} {leg.note}")
                    alerts += reconcile(leg, {})
                legs = [x for x in legs if x.client_order_id != leg.client_order_id]
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
    from app.portfolio.sleeve_replay import inputs
    from app.portfolio.sleeve_replay import state as replay_state
    from app.portfolio.stock_universe import allowed
    policy_alerts: list[str] = []
    try:
        allowed_stocks = allowed(out.parent / "paper_autopilot" / "stock_policy.json")
    except (OSError, ValueError, KeyError, TypeError):
        allowed_stocks = set()
        policy_alerts.append("AI stock-entry policy invalid: all new entries blocked; exits remain available")
    before = replay_state(st)
    for n in sleeve.step(st, recs, p.open, p.close, SECTOR_ETF, now, mode, allowed_stocks):
        print("ai picks:", n)
    replay_input = inputs(before, st, recs, p.open, p.close, now, mode)
    replay_input["allowed_stocks"] = sorted(allowed_stocks) if allowed_stocks is not None else None
    completed = p.close.loc[pd.DatetimeIndex(p.close.index).date < now.date()]
    references = {str(a): float(values.dropna().iloc[-1]) for a, values in completed.items() if values.notna().any()}
    alerts = mirror(st, client, pd.DatetimeIndex(p.open.index), now, dry, mode, references, allowed_stocks) if client else []
    alerts += policy_alerts
    st["broker_audit_status"] = "dry" if dry else "no_keys" if client is None else "checked"
    out.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1) + "\n")
    tmp.replace(path)
    Ledger(out / "replay_inputs.jsonl").append("sleeve_step", **replay_input)
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
    client: Alpaca | None = None
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
    from app.forward.step_result import emit
    emit("failed" if alerts else "warning" if client is None else "ok", alerts=alerts,
         broker_available=client is not None)
    if alerts:
        raise SystemExit(1)  # never mark a broken paper hedge as a clean event run


if __name__ == "__main__":
    main()
