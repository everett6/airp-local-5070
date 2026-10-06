"""Day-trading feasibility of the 150 technology companies (app/portfolio/day_feasibility.py). Market data only.

    python scripts/day_feasibility.py      # writes results/forward/day_feasibility.json

Daily bars (60 sessions) and the quoted spread in the last minute before 15:00 New York time of the latest session
come from Alpaca's free IEX feed, read with the main paper keys (data endpoints only, no orders). Dollar volume is
the consolidated 60-session median kept in the universe file. A side step of the scheduled event run.
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from app.forward.ledger import write_atomic
from app.portfolio.broker import DATA, PAPER, Alpaca
from app.portfolio.day_feasibility import metrics, verdict

OUT = BACKEND / "results" / "forward" / "day_feasibility.json"
NY = ZoneInfo("America/New_York")


def chunked(xs: list[str], n: int) -> list[list[str]]:
    return [xs[i:i + n] for i in range(0, len(xs), n)]


def main() -> None:
    from full_auto import universe
    companies = universe()
    client = Alpaca.from_env()
    if client is None:
        raise SystemExit("day feasibility: skipped, no Alpaca paper keys (market data needs them)")
    syms = [c["ticker"].replace("-", ".") for c in companies]
    now = datetime.now(UTC)
    bars: dict[str, list[dict[str, float]]] = {}
    for part in chunked(syms, 50):
        token = None
        while True:
            params: dict[str, Any] = {"symbols": ",".join(part), "timeframe": "1Day", "feed": "iex", "limit": 10000,
                                      "start": (now - timedelta(days=100)).date().isoformat()}
            if token:
                params["page_token"] = token
            got = client._get(f"{DATA}/v2/stocks/bars", **params)
            for s, rows in (got.get("bars") or {}).items():
                bars.setdefault(s, []).extend({k: float(r[k]) for k in ("o", "h", "l", "c")} for r in rows)
            token = got.get("next_page_token")
            if not token:
                break
    cal = client._get(f"{PAPER}/calendar", start=(now - timedelta(days=10)).date().isoformat(),
                      end=now.astimezone(NY).date().isoformat())
    closed = [d["date"] for d in cal if datetime.fromisoformat(f"{d['date']}T{d['close']}").replace(tzinfo=NY) <= now]
    day = closed[-1]
    t0 = datetime.fromisoformat(f"{day}T14:59:00").replace(tzinfo=NY).astimezone(UTC)
    spreads: dict[str, list[float]] = {}
    for part in chunked(syms, 30):
        got = client._get(f"{DATA}/v2/stocks/quotes", symbols=",".join(part), feed="iex", limit=10000,
                          start=t0.isoformat().replace("+00:00", "Z"),
                          end=(t0 + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"))
        for s, qs in (got.get("quotes") or {}).items():
            for q in qs:
                bid, ask = float(q.get("bp") or 0), float(q.get("ap") or 0)
                if bid > 0 and ask >= bid:
                    spreads.setdefault(s, []).append((ask - bid) / ((ask + bid) / 2) * 1e4)
    client.c.close()
    rows = []
    for c in companies:
        s = c["ticker"].replace("-", ".")
        sp = median(spreads[s]) if spreads.get(s) else None
        rows.append({"ticker": c["ticker"], "name": c["name"],
                     **verdict(metrics(bars.get(s, []), sp, c.get("dollar_volume")))})
    rows.sort(key=lambda r: (not r["feasible"], -(r.get("range_to_cost") or 0)))
    out = {"at": now.isoformat(), "spread_minute": t0.isoformat(), "feasible": sum(r["feasible"] for r in rows),
           "total": len(rows), "note": "feasibility only (cheap to trade), not a return forecast", "stocks": rows}
    write_atomic(OUT, json.dumps(out, indent=1) + "\n")
    print(f"day feasibility: {out['feasible']} of {out['total']} technology stocks can be day-traded cheaply")


if __name__ == "__main__":
    main()
