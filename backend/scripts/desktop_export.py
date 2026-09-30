"""Chart data for the airp desktop app: writes results/desktop/metrics.json (read-only research; nothing trades).

    python scripts/desktop_export.py        # ~30 s; also run daily by autorun's post-run hook after the 18:00 check

Everything is recomputed from files already on disk with the frozen code, so the app never sees a number that the
research record doesn't have:
- tracks: the backtested core book and the event (AI) book from results/planner/track_returns.parquet, plus SPY,
  as weekly equity indexes (100 at the start), drawdowns, rolling 1-year Sharpe, yearly returns and summary stats;
- lab: every pre-registered strategy test in results/daytrade_test.json (Sharpe, 95% CI, window, verdict), and the
  net equity curves of the calendar and day-trading tests that can be rebuilt cheaply (D9, T1, O1, E1);
- live: the forward allocator's equity per book, the AI-picks sleeve history, and the live AI decision scores.
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np
import pandas as pd

OUT = BACKEND / "results" / "desktop" / "metrics.json"
FWD = BACKEND / "results" / "forward"


def _r(x: float, d: int = 4) -> float | None:
    return None if x is None or not math.isfinite(float(x)) else round(float(x), d)


def curve(ret: pd.Series, per_year: int = 252) -> dict[str, Any]:
    """Weekly-sampled equity index, drawdown and rolling 1-year Sharpe, plus summary stats, from daily returns."""
    ret = ret.dropna().sort_index()
    eq = (1 + ret).cumprod() * 100
    dd = eq / eq.cummax() - 1
    roll = ret.rolling(per_year).mean() / ret.rolling(per_year).std() * np.sqrt(per_year)
    wk = pd.DataFrame({"eq": eq, "dd": dd, "rs": roll}).resample("W-FRI").last().dropna(subset=["eq"])
    years = len(ret) / per_year
    yearly = (1 + ret).groupby(ret.index.year).prod() - 1
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in wk.index],
        "equity": [_r(v, 2) for v in wk["eq"]], "drawdown": [_r(v) for v in wk["dd"]],
        "rolling_sharpe": [_r(v, 2) for v in wk["rs"]],
        "stats": {"cagr": _r((eq.iloc[-1] / 100) ** (1 / years) - 1),
                  "vol": _r(ret.std() * np.sqrt(per_year)), "sharpe": _r(ret.mean() / ret.std() * np.sqrt(per_year), 2),
                  "max_dd": _r(dd.min()), "start": ret.index[0].strftime("%Y-%m-%d"),
                  "end": ret.index[-1].strftime("%Y-%m-%d")},
        "yearly": {str(y): _r(v) for y, v in yearly.items()},
    }


def tracks() -> dict[str, Any]:
    t = pd.read_parquet(BACKEND / "results" / "planner" / "track_returns.parquet")
    t.index = pd.DatetimeIndex(t.index)
    spy = pd.read_parquet(BACKEND / "data" / "trend" / "etf_closes.parquet")["SPY"].pct_change()
    spy = spy.loc[t.index[0]:t.index[-1]]
    return {"core": curve(t["core"]), "event": curve(t["event"]), "SPY": curve(spy)}


def _stats_of(v: dict[str, Any]) -> dict[str, Any]:
    for key in ("1bp", "excess", "10bp", "5bp"):
        s = v.get(key)
        if isinstance(s, dict):
            s = s.get("both", s)
            if "sharpe" in s:
                return s
    for s in v.values():
        if isinstance(s, dict) and "sharpe" in s:
            return s
    return {}


GROUP = {"D": "day trading", "P": "pairs", "C": "crypto carry", "T": "calendar", "O": "calendar", "E": "calendar"}
NAMES = {"D1": "Intraday momentum", "D2": "Opening-range breakout", "D3": "Noise-band VWAP", "D4": "Rest-of-day momentum",
         "D5": "End-of-day reversal", "D6": "Box theory", "D7": "Intraday periodicity", "D8": "Darvas box",
         "D9": "Opening-auction reversal", "D10": "Pre-market reversal", "P1": "Pairs (GGR)", "C1": "Funding carry",
         "T1": "Turn of the month", "O1": "SPY overnight", "E1": "Macro announcements"}


def lab() -> dict[str, Any]:
    d = json.loads((BACKEND / "results" / "daytrade_test.json").read_text())
    tests = []
    for k, v in d.items():
        s = _stats_of(v)
        tests.append({"id": k, "name": NAMES.get(k, k), "group": GROUP.get(k[0], "other"), "window": v.get("window"),
                      "sharpe": s.get("sharpe"), "ci": s.get("ci"), "cagr": s.get("cagr"), "pass": bool(v.get("pass"))})
    curves: dict[str, Any] = {}
    try:  # rebuild the cheap net curves with the frozen functions
        from vol_target_b0 import tbill

        from app.sandbox.calendar_fx import tom_overlay
        from app.sandbox.overnight import overnight_excess, overnight_legs
        spy = pd.read_parquet(BACKEND / "data" / "trend" / "etf_closes.parquet")["SPY"].dropna()
        rf = tbill().reindex(spy.index, method="ffill").fillna(0.0) / 252
        ex = (spy.pct_change() - rf).dropna()
        curves["T1"] = curve(tom_overlay(ex, 1e-4)["ret"].loc["2008-01-02":])
        etf = pd.read_parquet(BACKEND / "data" / "statarb" / "sector_etfs.parquet")
        legs = overnight_legs(etf[("SPY", "Open")], etf[("SPY", "Close")])
        curves["O1"] = curve(overnight_excess(legs, rf.reindex(legs.index, method="ffill").fillna(0.0), 1e-4).loc["2012-01-03":])
        cal = BACKEND / "data" / "macro" / "announcements.csv"
        if cal.exists():
            from app.sandbox.macro_events import announcement_strategy, event_days
            x = ex.loc["2013-05-01":]
            ev = event_days(pd.DatetimeIndex(x.index), pd.read_csv(cal, dtype=str)["date"].tolist())
            curves["E1"] = curve(announcement_strategy(x, ev, 1e-4)["ret"])
    except Exception as e:  # noqa: BLE001 - a missing input drops a curve, never the export
        curves["error_calendar"] = f"{type(e).__name__}: {e}"[:200]
    try:
        from daytrade_test import daily_list

        from app.sandbox.intraday import d9_open_reversal
        daily, names = daily_list()
        curves["D9"] = curve(d9_open_reversal(daily, names, 1e-4)["ret"].loc["2016-01-04":])
    except Exception as e:  # noqa: BLE001
        curves["error_d9"] = f"{type(e).__name__}: {e}"[:200]
    return {"tests": tests, "curves": curves}


def _jsonl(p: Path) -> list[dict[str, Any]]:
    out = []
    if p.exists():
        for line in p.read_text(errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def live() -> dict[str, Any]:
    runs = _jsonl(FWD / "allocator" / "ledger.jsonl")
    books: dict[str, list[list[Any]]] = {}
    for r in runs:
        for name, b in (r.get("books") or {}).items():
            books.setdefault(name, []).append([r.get("data_through"), b.get("equity")])
    book = FWD / "ai_picks" / "book.json"
    ai = json.loads(book.read_text()) if book.exists() else {}
    dec = [r for r in _jsonl(FWD / "events" / "ledger.jsonl") if r.get("type") == "decision"]
    return {"books": books, "ai_history": ai.get("history", []), "ai_threshold": ai.get("threshold"),
            "decisions": [{"date": str(r.get("as_of", ""))[:10], "ticker": r.get("ticker"), "logodds": r.get("logodds")}
                          for r in dec]}


def main() -> None:
    t0 = time.monotonic()
    out = {"generated": datetime.now(UTC).isoformat(timespec="seconds"), "tracks": tracks(), "lab": lab(), "live": live()}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, separators=(",", ":")))
    tmp.replace(OUT)
    print(f"wrote {OUT.relative_to(BACKEND)} ({OUT.stat().st_size // 1024} KB) in {time.monotonic() - t0:.0f}s; "
          f"lab curves: {sorted(k for k in out['lab']['curves'] if not k.startswith('error'))}")


if __name__ == "__main__":
    main()
