"""One view of the paper account across the books that share it (docs/PLAN_60_V2.md, "Outside review, second part").

    python scripts/account_view.py            # read the account, write results/forward/account/view.json, print it
    python scripts/account_view.py --status   # print the last view without calling the broker

The mirrored book (90% of the account) and the AI-picks sleeve (10%) are kept in separate files but trade in one
Alpaca paper account: one margin limit, one buying power, and sector ETFs that several pairs short at once. This
reads the account (read-only: no order is sent or changed) and sets it against what the two books' own records say
they hold:
  - gross and net exposure, leverage, cash and buying power of the whole account;
  - per asset: what the broker holds, what each book's filled orders add up to, and the difference;
  - the sleeve's share of the account against its 10%, and each ETF hedge shared by more than one pair;
  - the broker's weight of each mirrored asset against the weight the mirror intended.
A position neither book can explain, or a book's position the broker does not have, is a "BROKER ALERT:" line.
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
sys.path.insert(0, str(BACKEND / "scripts"))

import httpx

from app.portfolio import sleeve
from app.portfolio.broker import PAPER, Alpaca, BrokerError, held_qty, position_asset

FWD = BACKEND / "results" / "forward"
DUST = 25.0          # dollars: a difference smaller than this is rounding (crypto to 6 decimals, fees), not a position
SLEEVE_ROOM = 2.5    # the sleeve's gross may reach 2 x its share (long and short legs); alert above 2.5 x


def book_holdings(orders: dict[str, Any]) -> dict[str, float]:
    """What the mirrored book's own order records add up to: buys filled minus sells filled, per asset."""
    out: dict[str, float] = {}
    for o in orders.values():
        for d in o.get("legs", []):
            q = held_qty(d.get("status", ""), d.get("qty", 0.0), d.get("filled_qty"))
            if q:
                out[d["asset"]] = out.get(d["asset"], 0.0) + (q if d["side"] == "buy" else -q)
    return {a: q for a, q in out.items() if abs(q) > 1e-9}


def sleeve_holdings(book: dict[str, Any]) -> tuple[dict[str, float], dict[str, list[str]]]:
    """What the sleeve's order records add up to (long stocks, short ETFs), and which pairs stand behind each asset."""
    from ai_picks import exposure
    qty: dict[str, float] = {}
    who: dict[str, list[str]] = {}
    for p in book.get("pairs", []):
        for a, q in exposure(p).items():
            qty[a] = qty.get(a, 0.0) + (q if a == p["ticker"] else -q)
            who.setdefault(a, []).append(p["ticker"])
    return qty, who


def build(acct: dict[str, Any], positions: list[dict[str, Any]], open_orders: list[dict[str, Any]],
          orders: dict[str, Any], picks: dict[str, Any], now: datetime) -> tuple[dict[str, Any], list[str]]:
    """The view and its alerts, from the broker's answers and the two books' files. Pure."""
    eq = float(acct.get("equity") or 0.0)
    pos = {position_asset(p["symbol"]): p for p in positions}
    price = {a: abs(float(p.get("market_value") or 0.0)) / abs(float(p["qty"])) for a, p in pos.items()
             if float(p.get("qty") or 0)}
    mirror, (slv, who) = book_holdings(orders), sleeve_holdings(picks)
    working = {position_asset(o["symbol"]) for o in open_orders}
    rows: list[dict[str, Any]] = []
    alerts: list[str] = []
    for a in sorted(set(pos) | set(mirror) | set(slv)):
        have = float(pos[a]["qty"]) if a in pos else 0.0
        mv = float(pos[a].get("market_value") or 0.0) if a in pos else 0.0
        gap = have - mirror.get(a, 0.0) - slv.get(a, 0.0)
        px = price.get(a)
        gap_usd = None if px is None else abs(gap) * px
        row = {"asset": a, "broker_qty": have, "market_value": round(mv, 2),
               "weight": round(mv / eq, 4) if eq else None, "mirror_qty": mirror.get(a, 0.0),
               "sleeve_qty": slv.get(a, 0.0), "unexplained_qty": round(gap, 6),
               "unexplained_usd": None if gap_usd is None else round(gap_usd, 2), "order_working": a in working}
        rows.append(row)
        big = abs(gap) > 1e-6 and (gap_usd is None or gap_usd > DUST)
        if big and a not in working:  # an order still working explains a difference for now
            alerts.append(f"account: {a} broker holds {have:g}, the books' orders add up to "
                          f"{mirror.get(a, 0.0) + slv.get(a, 0.0):g} (difference {gap:+g}"
                          + (f", about ${gap_usd:,.0f})" if gap_usd is not None else ")"))
    long_mv = sum(r["market_value"] for r in rows if r["market_value"] > 0)
    short_mv = -sum(r["market_value"] for r in rows if r["market_value"] < 0)
    s_long = sum(q * price.get(a, 0.0) for a, q in slv.items() if q > 0)
    s_short = -sum(q * price.get(a, 0.0) for a, q in slv.items() if q < 0)
    last = max(orders, default=None)
    intended = {}
    if last and orders[last].get("targets"):
        scale = float(orders[last].get("broker_scale") or 1.0)
        intended = {a: round(w * scale * (1 - sleeve.SHARE), 4) for a, w in orders[last]["targets"].items()}
    view = {
        "at": now.isoformat(timespec="seconds"), "equity": round(eq, 2), "cash": round(float(acct.get("cash") or 0), 2),
        "buying_power": round(float(acct.get("buying_power") or 0), 2),
        "regt_buying_power": round(float(acct.get("regt_buying_power") or 0), 2),
        "maintenance_margin": round(float(acct.get("maintenance_margin") or 0), 2),
        "long": round(long_mv, 2), "short": round(short_mv, 2), "gross": round(long_mv + short_mv, 2),
        "net": round(long_mv - short_mv, 2),
        "leverage": round((long_mv + short_mv) / eq, 3) if eq else None,
        "net_leverage": round((long_mv - short_mv) / eq, 3) if eq else None,
        "sleeve": {"long": round(s_long, 2), "short": round(s_short, 2), "gross": round(s_long + s_short, 2),
                   "net": round(s_long - s_short, 2), "target_share": sleeve.SHARE,
                   "gross_share": round((s_long + s_short) / eq, 4) if eq else None,
                   "open_pairs": sum(p["status"] in ("planned", "open") for p in picks.get("pairs", []))},
        "mirror": {"decision": last, "intended_weights": intended,
                   "broker_weights": {a: round(mirror.get(a, 0.0) * price.get(a, 0.0) / eq, 4) if eq else None
                                      for a in sorted(set(intended) | set(mirror))}},
        "shared_hedges": {a: {"pairs": sorted(v), "qty": slv[a]} for a, v in sorted(who.items())
                          if len(v) > 1 and slv.get(a, 0.0) < 0},
        "open_orders": len(open_orders), "positions": rows,
    }
    if eq and (s_long + s_short) / eq > SLEEVE_ROOM * sleeve.SHARE:
        alerts.append(f"account: the AI sleeve's gross is {100 * (s_long + s_short) / eq:.1f}% of the account "
                      f"(its share is {100 * sleeve.SHARE:.0f}%, long and short together up to twice that)")
    if float(acct.get("buying_power") or 0) < 0:
        alerts.append(f"account: buying power is negative ({float(acct['buying_power']):,.0f})")
    view["alerts"] = alerts
    return view, alerts


def read_json(path: Path) -> dict[str, Any]:
    try:
        return dict(json.loads(path.read_text()))
    except (OSError, ValueError):
        return {}


def run(client: Alpaca, out: Path, now: datetime, broker_dir: Path, picks_dir: Path) -> list[str]:
    acct = client._get(f"{PAPER}/account")
    positions = client._get(f"{PAPER}/positions")
    open_orders = client._get(f"{PAPER}/orders", status="open", limit=500)
    view, alerts = build(acct, positions, open_orders, read_json(broker_dir / "orders.json"),
                         read_json(picks_dir / "book.json"), now)
    out.mkdir(parents=True, exist_ok=True)
    tmp = out / "view.json.tmp"
    tmp.write_text(json.dumps(view, indent=1) + "\n")
    tmp.replace(out / "view.json")
    with (out / "history.jsonl").open("a") as f:  # one line per run: how exposure and leverage moved
        f.write(json.dumps({k: view[k] for k in ("at", "equity", "gross", "net", "leverage", "net_leverage",
                                                 "buying_power")} | {"sleeve_gross": view["sleeve"]["gross"],
                                                                     "unexplained": len(alerts)}) + "\n")
    show(view)
    return alerts


def show(v: dict[str, Any]) -> None:
    print(f"account: equity {v['equity']:,.0f}  gross {v['gross']:,.0f} ({v['leverage']}x)  net {v['net']:,.0f} "
          f"({v['net_leverage']}x)  buying power {v['buying_power']:,.0f}  open orders {v['open_orders']}")
    s = v["sleeve"]
    print(f"  AI sleeve: long {s['long']:,.0f}  short {s['short']:,.0f}  gross {100 * (s['gross_share'] or 0):.1f}% "
          f"of the account (share {100 * s['target_share']:.0f}%), {s['open_pairs']} pair(s)")
    for a, h in v["shared_hedges"].items():
        print(f"  shared hedge {a}: {h['qty']:g} for {', '.join(h['pairs'])}")
    for r in v["positions"]:
        flag = "" if abs(r["unexplained_qty"]) < 1e-6 else f"   <- {r['unexplained_qty']:+g} unexplained"
        print(f"  {r['asset']:8} broker {r['broker_qty']:>12g}  mirror {r['mirror_qty']:>12g}  sleeve "
              f"{r['sleeve_qty']:>8g}  value {r['market_value']:>12,.0f}{flag}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="results/forward/account")
    ap.add_argument("--broker", default="results/forward/broker")
    ap.add_argument("--picks", default="results/forward/ai_picks")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    out = BACKEND / a.out
    if a.status:
        v = read_json(out / "view.json")
        if v:
            show(v)
        else:
            print("account: no view yet")
        return
    alerts: list[str] = []
    try:
        client = Alpaca.from_env()
        if client is None:
            print("account: skipped (no Alpaca paper keys in backend/.env)")
            return
        alerts = run(client, out, datetime.now(UTC), BACKEND / a.broker, BACKEND / a.picks)
    except (BrokerError, httpx.HTTPError, OSError, ValueError, KeyError) as e:
        print(f"account: view failed: {type(e).__name__}: {e}"[:300])
    for x in alerts:
        print("BROKER ALERT:", x)


if __name__ == "__main__":
    main()
