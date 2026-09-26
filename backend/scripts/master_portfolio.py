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


def book_events(ev: pd.DataFrame, dec_path: str, h: int, stocks: Prices, days: pd.DatetimeIndex) -> list[dict]:
    """Every scored release of one book: entry day, its h-day outcome vs the sector and when that became known."""
    dec = {json.loads(x)["accession"]: json.loads(x) for x in (BACKEND / dec_path).read_text().splitlines()}
    evs = []
    for r in ev.itertuples():
        d = dec.get(r.accession)
        etf = SECTOR_ETF.get(str(r.sector))
        t = str(r.ticker).replace(".", "-")
        i = entry_index(days, datetime.fromisoformat(str(r.accepted_utc))) if d else None
        if d is None or etf is None or i is None or t not in stocks.open.columns:
            continue
        out = fwd_excess(stocks, t, etf, i, h)
        evs.append({"i": i, "ticker": t, "sector": r.sector, "logodds": d["logodds"], "out": out, "h": h,
                    "known_at": days[i + h].date() if i + h < len(days) and out is not None else None})
    return sorted(evs, key=lambda e: e["i"])


def full_mode(args: argparse.Namespace) -> dict:
    """One or more books (--book decisions.jsonl:horizon, repeatable; default --decide at --horizon). Each book has its
    own calibrator (fed only outcomes known by the day) and holding period; all share the stock sleeve's caps. A
    ticker picked by two books is held once, sized by the more confident book."""
    ev = pd.read_csv(BACKEND / args.events)
    stocks = Prices.from_long(pd.read_parquet(BACKEND / args.prices))
    crypto = Prices.from_long(load_crypto(args.end))
    opens = stocks.open.join(crypto.open[["BTC-USD", "ETH-USD"]], how="left")
    closes = stocks.close.join(crypto.close[["BTC-USD", "ETH-USD"]], how="left")
    days = pd.DatetimeIndex(stocks.open.index)
    cfg = MasterConfig(crypto_cap=args.crypto_cap)
    specs = [(b.rsplit(":", 1)[0], int(b.rsplit(":", 1)[1])) for b in args.book] or [(args.decide, args.horizon)]
    books = [{"name": f"{Path(pth).stem}@{h}d", "evs": book_events(ev, pth, h, stocks, days), "h": h,
              "cal": Calibrator(min_rows=args.min_calibration), "k": 0, "ps": []} for pth, h in specs]
    targets: dict[date, dict[str, float]] = {}
    base_t: dict[date, dict[str, float]] = {}
    held: dict[tuple[str, str], dict] = {}
    start_i = days.searchsorted(pd.Timestamp(args.start))
    for i in range(start_i, len(days) - 1):
        d = days[i].date()
        for b in books:
            for e in b["evs"]:  # outcomes that became known today feed that book's calibrator (never earlier)
                if e["known_at"] == d:
                    b["cal"].add(d, e["logodds"], e["out"] > 0)
            # a release whose first tradable open is tomorrow's joins the targets set tonight; those are traded at
            # tomorrow's open, which is after the release (entry_index); everything else uses closes up to today
            while b["k"] < len(b["evs"]) and b["evs"][b["k"]]["i"] <= i + 1:
                e = b["evs"][b["k"]]
                b["k"] += 1
                if e["i"] != i + 1:
                    continue
                p = b["cal"].prob(e["logodds"], d)
                c = stocks.close[e["ticker"]].iloc[max(0, i - 60):i + 1].pct_change().std() * math.sqrt(252)
                if p is None or np.isnan(c):
                    continue
                weight = None
                if args.sizing == "top5th":
                    # the rank rule the signal tests use: top fifth of this book's calibrated p over the previous 90
                    # days (earlier releases only), equal weight
                    recent = [q for dd, q in b["ps"] if (d - dd).days <= 90]
                    b["ps"].append((d, p))
                    if len(recent) < 30 or p < float(np.quantile(recent, 0.8)):
                        continue
                    weight = args.pick_weight
                held[(e["ticker"], b["name"])] = {
                    "cand": Candidate(e["ticker"], p, float(c), e["sector"], b["name"],
                                      base=b["cal"].base_rate(d), weight=weight),
                    "until": days[min(len(days) - 1, i + b["h"] + 1)].date()}
        held = {k: v for k, v in held.items() if v["until"] > d}
        best: dict[str, Candidate] = {}
        for v in held.values():
            c0 = v["cand"]
            if c0.asset not in best or c0.p > best[c0.asset].p:
                best[c0.asset] = c0
        st = crypto_state(closes, days[i], cfg.crypto_assets)
        targets[d] = allocate(list(best.values()), st, cfg).weights
        base_t[d] = allocate([], st, cfg).weights
    s0, s1 = days[start_i].date(), days[-1].date()
    master = simulate_weights(targets, opens, closes, s0, s1, cost_bps=args.cost_bps)
    base = simulate_weights(base_t, opens, closes, s0, s1, cost_bps=args.cost_bps)
    spy = simulate_weights({s0: {"SPY": 0.98}}, opens, closes, s0, s1, cost_bps=args.cost_bps)
    stock_days = sum(any(a not in ("SPY", "BTC-USD", "ETH-USD") for a in w) for w in targets.values())
    return {"mode": "full", "window": [s0.isoformat(), s1.isoformat()], "books": [b["name"] for b in books],
            "sizing": args.sizing, "days_holding_stocks_pct": round(100 * stock_days / max(1, len(targets)), 1),
            "events_scored": sum(len(b["evs"]) for b in books), "cost_bps": args.cost_bps,
            "results": [stats(master, "master portfolio"), stats(base, "SPY + crypto sleeve (no stocks)"),
                        stats(spy, "SPY")],
            "yearly": {"master": yearly(master), "SPY + crypto": yearly(base), "SPY": yearly(spy)}}


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
    ap.add_argument("--book", action="append", default=[], help="decisions.jsonl:horizon, repeatable (three books)")
    ap.add_argument("--cost-bps", type=float, default=5.0, help="per-trade cost + slippage in basis points")
    ap.add_argument("--sizing", choices=("kelly", "top5th"), default="kelly",
                    help="kelly: quarter Kelly on the edge over the base rate; top5th: equal-weight top fifth")
    ap.add_argument("--pick-weight", type=float, default=0.025, help="per-pick weight for --sizing top5th")
    ap.add_argument("--tag", default="", help="suffix for results/master_full<tag>.json")
    args = ap.parse_args()
    out = crypto_mode(args) if args.mode == "crypto" else full_mode(args)
    (BACKEND / "results" / f"master_{args.mode}{args.tag}.json").write_text(json.dumps(out, indent=1, default=str) + "\n")
    print(f"{out['mode']}: {out['window'][0]} -> {out['window'][1]}"
          + (f"  sizing={out['sizing']}, days holding stocks {out['days_holding_stocks_pct']}%" if "sizing" in out else ""))
    print(f"{'portfolio':42s} {'total':>8s} {'CAGR':>7s} {'vol':>6s} {'Sharpe':>6s} {'maxDD':>6s}")
    for r in out["results"]:
        print(f"{r['name']:42s} {r['total_return_pct']:>7.1f}% {r['cagr_pct']:>6.2f}% {r['vol_pct']:>5.1f}% "
              f"{r['sharpe']:>6.2f} {r['max_drawdown_pct']:>5.1f}%")
    for name, ys in out["yearly"].items():
        print(f"  {name[:40]:40s} " + " ".join(f"{y}:{v:+.0f}%" for y, v in ys.items()))


if __name__ == "__main__":
    main()
