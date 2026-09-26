"""Master-agent portfolio backtests (paper money, free Yahoo data).

    python scripts/master_portfolio.py crypto            # SPY core + crypto trend sleeve, 2018 -> now
    python scripts/master_portfolio.py full --decide results/events/decide_bonsai-27b_latest.jsonl
                                                         # + earnings-event stock picks (clean window)

crypto  weekly: the crypto sub-agent's trend state for BTC and ETH -> master allocation (crypto <= 20%, rest SPY).
        Benchmarks: SPY, a fixed 80/20 SPY/BTC mix rebalanced weekly, BTC buy-and-hold.
full    daily: new earnings-event BUYs (Bonsai log-odds, calibrated on events whose --horizon outcome was already known)
        become --horizon-day positions (5 quick money, 20 mid term, 120 long term) sized by the master agent, plus the crypto sleeve, plus the SPY core.
Crypto trades only on US trading days here (weekend moves show up at Monday's price). Writes
results/master_<mode>.json.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import numpy as np
import pandas as pd
import yfinance as yf

from app.portfolio.master import (
    Calibrator,
    Candidate,
    MasterConfig,
    WeightSim,
    allocate,
    crypto_state,
    simulate_weights,
)
from app.sandbox.events import Prices, entry_index, fwd_excess

CRYPTO_FILE = BACKEND / "data" / "crypto" / "ohlcv_crypto.parquet"
SECTOR_ETF = {"Information Technology": "XLK", "Financials": "XLF", "Health Care": "XLV",
              "Consumer Discretionary": "XLY", "Consumer Staples": "XLP", "Energy": "XLE", "Industrials": "XLI",
              "Materials": "XLB", "Utilities": "XLU", "Real Estate": "XLRE", "Communication Services": "XLC"}


def load_crypto(end: str) -> pd.DataFrame:
    if not CRYPTO_FILE.exists():
        CRYPTO_FILE.parent.mkdir(parents=True, exist_ok=True)
        frames = []
        for t in ("BTC-USD", "ETH-USD", "SPY"):
            df = yf.download(t, start="2016-01-01", end=end, auto_adjust=True, progress=False, multi_level_index=False)
            frames.append(df[["Open", "High", "Low", "Close", "Volume"]].assign(Ticker=t))
        long = pd.concat(frames).rename_axis("Date").reset_index()
        long["Date"] = pd.to_datetime(long["Date"]).dt.date.astype(str)
        long.to_parquet(CRYPTO_FILE, index=False)
    return pd.read_parquet(CRYPTO_FILE)


def stats(sim: WeightSim, name: str) -> dict[str, float | str]:
    eq = np.asarray(sim.equity)
    r = np.diff(eq) / eq[:-1]
    years = (sim.days[-1] - sim.days[0]).days / 365.25
    peak = np.maximum.accumulate(eq)
    return {"name": name, "total_return_pct": round(100 * (eq[-1] / eq[0] - 1), 1),
            "cagr_pct": round(100 * ((eq[-1] / eq[0]) ** (1 / years) - 1), 2),
            "vol_pct": round(100 * float(r.std() * math.sqrt(252)), 1),
            "sharpe": round(float(r.mean() / r.std() * math.sqrt(252)), 2) if r.std() > 0 else 0.0,
            "max_drawdown_pct": round(100 * float((1 - eq / peak).max()), 1), "trades": sim.trades}


def yearly(sim: WeightSim) -> dict[int, float]:
    s = pd.Series(sim.equity, index=pd.to_datetime(sim.days))
    ends = s.groupby(s.index.year).last()
    prev = pd.concat([pd.Series([sim.equity[0]]), ends.iloc[:-1]]).to_numpy()
    return {int(y): round(100 * (v / p - 1), 1) for (y, v), p in zip(ends.items(), prev, strict=True)}


def crypto_mode(args: argparse.Namespace) -> dict:
    px = Prices.from_long(load_crypto(args.end))
    cfg = MasterConfig(crypto_cap=args.crypto_cap)
    days = [d for d in px.close.index if d.date() >= date.fromisoformat(args.start)]
    weekly = days[::5]
    targets, spy_t, mix_t, btc_t, log = {}, {}, {}, {}, []
    for d in weekly:
        st = crypto_state(px.close, d, cfg.crypto_assets)
        a = allocate([], st, cfg)
        targets[d.date()] = a.weights
        log.append({"day": d.date().isoformat(), "weights": {k: round(v, 3) for k, v in a.weights.items()},
                    "off": list(a.dropped)})
        spy_t[d.date()] = {"SPY": 0.98}
        mix_t[d.date()] = {"SPY": 0.78, "BTC-USD": 0.20}
        btc_t[d.date()] = {"BTC-USD": 0.98}
    s0, s1 = weekly[0].date(), days[-1].date()
    runs = {"master (SPY core + crypto trend sleeve)": simulate_weights(targets, px.open, px.close, s0, s1),
            "SPY": simulate_weights(spy_t, px.open, px.close, s0, s1),
            "fixed 80/20 SPY/BTC": simulate_weights(mix_t, px.open, px.close, s0, s1),
            "BTC buy and hold": simulate_weights({s0: {"BTC-USD": 0.98}}, px.open, px.close, s0, s1)}
    on_share = np.mean([len(x["off"]) < 2 for x in log])
    return {"mode": "crypto", "window": [s0.isoformat(), s1.isoformat()], "config": cfg.__dict__,
            "results": [stats(v, k) for k, v in runs.items()], "yearly": {k: yearly(v) for k, v in runs.items()},
            "weeks_with_some_crypto_pct": round(100 * float(on_share), 1), "allocations": log[-8:]}


def full_mode(args: argparse.Namespace) -> dict:
    ev = pd.read_csv(BACKEND / args.events)
    dec = {json.loads(x)["accession"]: json.loads(x) for x in (BACKEND / args.decide).read_text().splitlines()}
    stocks = Prices.from_long(pd.read_parquet(BACKEND / args.prices))
    crypto = Prices.from_long(load_crypto(args.end))
    opens = stocks.open.join(crypto.open[["BTC-USD", "ETH-USD"]], how="left")
    closes = stocks.close.join(crypto.close[["BTC-USD", "ETH-USD"]], how="left")
    days = pd.DatetimeIndex(stocks.open.index)
    cfg = MasterConfig(crypto_cap=args.crypto_cap)
    cal = Calibrator(min_rows=args.min_calibration)
    # every scored event: entry day, 20-day outcome (known 20 trading days after entry), sector ETF
    evs = []
    for r in ev.itertuples():
        d = dec.get(r.accession)
        etf = SECTOR_ETF.get(str(r.sector))
        t = str(r.ticker).replace(".", "-")
        i = entry_index(days, datetime.fromisoformat(str(r.accepted_utc))) if d else None
        if d is None or etf is None or i is None or t not in stocks.open.columns:
            continue
        h = args.horizon
        out = fwd_excess(stocks, t, etf, i, h)
        evs.append({"i": i, "ticker": t, "sector": r.sector, "logodds": d["logodds"], "out": out,
                    "known_at": days[i + h].date() if i + h < len(days) and out is not None else None})
    evs.sort(key=lambda e: e["i"])
    targets: dict[date, dict[str, float]] = {}
    held: dict[str, dict] = {}
    k = 0
    start_i = days.searchsorted(pd.Timestamp(args.start))
    for i in range(start_i, len(days) - 1):
        d = days[i].date()
        for e in evs:  # outcomes that became known today feed the calibrator (never earlier)
            if e["known_at"] == d:
                cal.add(d, e["logodds"], e["out"] > 0)
        # a release whose first tradable open is tomorrow's joins the targets set tonight; those targets are traded at
        # tomorrow's open, which is after the release (entry_index), and everything else here uses closes up to today
        while k < len(evs) and evs[k]["i"] <= i + 1:
            e = evs[k]
            k += 1
            if e["i"] != i + 1:
                continue
            p = cal.prob(e["logodds"], d)
            c = stocks.close[e["ticker"]].iloc[max(0, i - 60):i + 1].pct_change().std() * math.sqrt(252)
            if p is not None and not np.isnan(c):
                held[e["ticker"]] = {"cand": Candidate(e["ticker"], p, float(c), e["sector"], "bonsai"),
                                     "until": days[min(len(days) - 1, i + args.horizon + 1)].date()}
        held = {t: h for t, h in held.items() if h["until"] > d}
        a = allocate([h["cand"] for h in held.values()], crypto_state(closes, days[i], cfg.crypto_assets), cfg)
        targets[d] = a.weights
    s0, s1 = days[start_i].date(), days[-1].date()
    master = simulate_weights(targets, opens, closes, s0, s1)
    spy = simulate_weights({s0: {"SPY": 0.98}}, opens, closes, s0, s1)
    return {"mode": "full", "window": [s0.isoformat(), s1.isoformat()], "events_scored": len(evs),
            "results": [stats(master, "master portfolio"), stats(spy, "SPY")],
            "yearly": {"master": yearly(master), "SPY": yearly(spy)}}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("crypto", "full"))
    ap.add_argument("--start", default="2018-01-02")
    ap.add_argument("--end", default="2026-09-25")
    ap.add_argument("--crypto-cap", type=float, default=0.20)
    ap.add_argument("--events", default="data/events/events_2024-01-01_2026-09-24.csv")
    ap.add_argument("--prices", default="data/events/ohlcv_2023-01-01_2026-09-25.parquet")
    ap.add_argument("--decide", default="results/events/decide_bonsai-27b_latest.jsonl")
    ap.add_argument("--min-calibration", type=int, default=300)
    ap.add_argument("--horizon", type=int, default=20, help="holding period and outcome in trading days (5/20/120)")
    ap.add_argument("--tag", default="", help="suffix for results/master_full<tag>.json")
    args = ap.parse_args()
    out = crypto_mode(args) if args.mode == "crypto" else full_mode(args)
    (BACKEND / "results" / f"master_{args.mode}{args.tag}.json").write_text(json.dumps(out, indent=1, default=str) + "\n")
    print(f"{out['mode']}: {out['window'][0]} -> {out['window'][1]}")
    print(f"{'portfolio':42s} {'total':>8s} {'CAGR':>7s} {'vol':>6s} {'Sharpe':>6s} {'maxDD':>6s}")
    for r in out["results"]:
        print(f"{r['name']:42s} {r['total_return_pct']:>7.1f}% {r['cagr_pct']:>6.2f}% {r['vol_pct']:>5.1f}% "
              f"{r['sharpe']:>6.2f} {r['max_drawdown_pct']:>5.1f}%")
    for name, ys in out["yearly"].items():
        print(f"  {name[:40]:40s} " + " ".join(f"{y}:{v:+.0f}%" for y, v in ys.items()))


if __name__ == "__main__":
    main()
