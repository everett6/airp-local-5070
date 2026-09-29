"""Daily return histories of each strategy track, for the goal planner (app/portfolio/planner.py).

    python scripts/track_returns.py        # writes results/planner/track_returns.parquet (+ a summary json)

- core: the master book (SPY + crypto trend) with the drawdown brakes, 2018-2026 backtest (drawdown_brakes.py).
- event: the AI-picks sleeve's exact rules replayed day by day on the 2024 and 2025-26 samples (two runs a day,
  slots and costs), as a return on the sleeve's own capital. A replay, not a passed test.
Tracks with no history (long-term picks, day trading) are simply absent: the planner treats them as "no evidence".
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import pandas as pd
from bonsai_lite import SAMPLES
from drawdown_brakes import brake
from event_eval import SECTOR_ETF
from forward_events import entry_deadline
from secchk_eval import load
from trend_sleeve import master_b0

from app.portfolio import sleeve
from app.sandbox.events import Prices

OUT = BACKEND / "results" / "planner"


def replay_event(tag: str, events: str, p: Prices) -> pd.Series:
    ev = pd.read_csv(BACKEND / events).merge(load(tag, 5), on="accession")
    decs = []
    for r in ev.itertuples():
        acc = pd.Timestamp(r.accepted_utc)
        acc = acc.tz_localize("UTC") if acc.tzinfo is None else acc
        decs.append({"type": "decision", "accession": r.accession, "ticker": r.ticker, "sector": r.sector,
                     "source": "bonsai", "logodds": float(r.logodds), "_acc": acc,
                     "entry_deadline": entry_deadline(str(r.accepted_utc)).isoformat()})
    decs.sort(key=lambda d: d["_acc"])
    days = p.open.index
    st = sleeve.new_state()
    start = decs[0]["_acc"].normalize().tz_localize(None)
    k_vis = 0
    for day in days[days.searchsorted(start):]:
        for hh in (12, 22):  # the 08:45 and 18:30 ET runs, in UTC hours
            now = datetime(day.year, day.month, day.day, hh, 45, tzinfo=UTC)
            k = int(days.searchsorted(day)) + (1 if hh == 22 else 0)
            while k_vis < len(decs) and decs[k_vis]["_acc"] < now:
                k_vis += 1
            sleeve.step(st, decs[:k_vis], p.open.iloc[:k], p.close.iloc[:k], SECTOR_ETF, now)
    eq = pd.Series({pd.Timestamp(h["day"]): h["equity"] for h in st["history"]})
    return eq.pct_change().dropna()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    core, _ = brake(master_b0("2018-01-02", "2026-09-26"))
    p = Prices.from_long(pd.read_parquet(BACKEND / "data/events/ohlcv_2023-01-01_2026-09-25.parquet"))
    parts = [replay_event(tag, events, p) for tag, events, _ in SAMPLES.values()]  # 2024, then 2025-26
    event = pd.concat([parts[0][parts[0].index < parts[1].index[0]], parts[1]])  # 2024's flat tail is dropped
    df = pd.DataFrame({"core": core, "event": event})
    df.index = pd.DatetimeIndex(df.index)
    df.to_parquet(OUT / "track_returns.parquet")
    summ = {c: {"start": str(df[c].dropna().index[0].date()), "end": str(df[c].dropna().index[-1].date()),
                "days": int(df[c].notna().sum()),
                "cagr": round(float((1 + df[c].dropna()).prod() ** (252 / df[c].notna().sum()) - 1), 4),
                "vol": round(float(df[c].std() * 252 ** 0.5), 4)} for c in df.columns}
    (OUT / "track_returns.json").write_text(json.dumps(summ, indent=1) + "\n")
    print(json.dumps(summ, indent=1))


if __name__ == "__main__":
    main()
