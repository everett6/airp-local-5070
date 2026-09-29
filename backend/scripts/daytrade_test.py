"""The day-trading track's pre-registered test (docs/PLAN_60_V2.md "Day-trading track"): D1 and D2, one trial each.

    python scripts/daytrade_test.py --rules D3,D4   # round 2; needs data/intraday/{SPY,QQQ}_1min.parquet
    python scripts/daytrade_test.py --rules D6,D7,D8   # round 4 (98.3% CI: Bonferroni over its 3 trials)
Each rule is one registered trial: run a rule once. Results merge into results/daytrade_test.json.

Pass (each rule): on its post-publication window, at 1 bp a side, the annualized Sharpe of the daily P&L (SPY and
QQQ, equal capital) is >= 0.5 AND its 95% block-bootstrap CI is above 0. Writes results/daytrade_test.json.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import pandas as pd

from app.sandbox.dsr import register
from app.sandbox.intraday import (
    block_ci,
    d1_intraday_momentum,
    d2_orb5,
    d3_noise_vwap,
    d4_rod_momentum,
    d5_eod_reversal,
    d6_box_theory,
    d7_periodicity,
    d8_darvas,
    sharpe,
)

DATA = BACKEND / "data" / "intraday"
RULES = {"D1": (d1_intraday_momentum, "2019-01-02", "daytrade_intraday_momentum"),
         "D2": (d2_orb5, "2023-07-01", "daytrade_orb5"),
         "D3": (d3_noise_vwap, "2024-06-01", "daytrade_noise_vwap"),
         "D4": (d4_rod_momentum, "2021-11-01", "daytrade_rod_momentum"),
         "D6": (d6_box_theory, "2016-01-05", "daytrade_box_theory")}
ROUND4 = {"D6", "D7", "D8"}
LEVEL4 = 1 - 0.05 / 3  # round 4: Bonferroni over its 3 trials
END = "2026-09-25"


def stats(r: pd.Series, level: float = 0.95) -> dict:
    lo, hi = block_ci(r, level=level)
    m = (1 + r).groupby(r.index.to_period("M")).prod() - 1
    return {"days": len(r), "cagr": round(float((1 + r).prod() ** (252 / len(r)) - 1), 4),
            "sharpe": round(sharpe(r), 3), "ci": [round(lo, 3), round(hi, 3)],
            "hit_rate": round(float((r > 0).mean()), 3), "worst_month": round(float(m.min()), 4),
            "trade_days_per_week": round(float((r != 0).mean() * 5), 2), "ci_level": round(level, 4)}


D5_START, D5_TRIAL = "2024-07-01", "daytrade_eod_reversal"


def run_d5(core: pd.Series) -> dict:
    """D5 (cross-section, 30-minute bars of the S&P 500 list): one trial, same pass rule."""
    df = pd.read_parquet(DATA / "sp500_30min.parquet")
    res: dict = {"window": [D5_START, END]}
    for bp in (1, 3):
        r = d5_eod_reversal(df, bp / 1e4)
        test = r.loc[D5_START:END]
        res[f"{bp}bp"] = {"both": stats(test["ret"]), "long_leg_gross": round(float(test["long"].mean()) * 1e4, 2),
                          "short_leg_gross": round(float(test["short"].mean()) * 1e4, 2),
                          "names_median": int(test["names"].median())}
        if bp == 1:
            res["before_window"] = stats(r.loc[:D5_START, "ret"].iloc[:-1])
            res["corr_with_core"] = round(float(test["ret"].corr(core.reindex(test.index))), 3)
    t = res["1bp"]["both"]
    res["pass"] = bool(t["sharpe"] >= 0.5 and t["ci"][0] > 0)
    register({"trial": D5_TRIAL, "date": time.strftime("%Y-%m-%d"), "kind": "daytrade", "sharpe_ann": t["sharpe"],
              "window": f"{D5_START}..{END}", "result": "pass" if res["pass"] else "fail"})
    print(f"D5 {D5_TRIAL}: Sharpe {t['sharpe']} CI {t['ci']} CAGR {t['cagr']:.1%} hit {t['hit_rate']:.0%} "
          f"(3 bps: Sharpe {res['3bp']['both']['sharpe']}) -> {'PASS' if res['pass'] else 'FAIL'}", flush=True)
    return res


D7_START, D7_TRIAL = "2016-02-01", "daytrade_periodicity"
D8_START, D8_END, D8_TRIAL = "2024-07-01", "2026-09-24", "daytrade_darvas_box"


def run_d7(core: pd.Series) -> dict:
    """D7 (intraday periodicity, the 15:30 slot of D5's data): one trial, round-4 pass rule."""
    df = pd.read_parquet(DATA / "sp500_30min.parquet")
    res: dict = {"window": [D7_START, END]}
    for bp in (1, 3):
        test = d7_periodicity(df, bp / 1e4).loc[D7_START:END]
        res[f"{bp}bp"] = {"both": stats(test["ret"], LEVEL4),
                          "long_leg_gross": round(float(test["long"].mean()) * 1e4, 2),
                          "short_leg_gross": round(float(test["short"].mean()) * 1e4, 2),
                          "names_median": int(test["names"].median())}
        if bp == 1:
            res["since_2024_07"] = stats(test.loc["2024-07-01":, "ret"], LEVEL4)
            res["corr_with_core"] = round(float(test["ret"].corr(core.reindex(test.index))), 3)
    return finish("D7", D7_TRIAL, D7_START, END, res, res["1bp"]["both"], f"3 bps: Sharpe {res['3bp']['both']['sharpe']}")


def run_d8(core: pd.Series) -> dict:
    """D8 (Darvas box book on daily bars of D5's list, excess over SPY): one trial, round-4 pass rule."""
    from intraday_stocks import universe

    daily = pd.read_parquet(BACKEND / "data" / "events" / "ohlcv_2009-01-01_2026-09-25.parquet")
    daily = daily[daily["Date"] >= "2014-01-01"]
    names = universe()
    daily = daily[daily["Ticker"].isin(set(names) | {"SPY"})].assign(Date=lambda d: pd.to_datetime(d["Date"]))
    r = d8_darvas(daily, names, cost=0.001)
    test = r.loc[D8_START:D8_END]
    res: dict = {"window": [D8_START, D8_END], "excess": stats(test["excess"], LEVEL4),
                 "book": stats(test["ret"], LEVEL4), "spy": stats(test["spy"], LEVEL4),
                 "held_median": int(test["held"].median()),
                 "before_window_excess": stats(r.loc["2016-01-01":D8_START, "excess"].iloc[:-1], LEVEL4),
                 "corr_with_core": round(float(test["excess"].corr(core.reindex(test.index))), 3)}
    return finish("D8", D8_TRIAL, D8_START, D8_END, res, res["excess"], f"book CAGR {res['book']['cagr']:.1%}, "
                  f"SPY {res['spy']['cagr']:.1%}, median held {res['held_median']}")


def finish(name: str, trial: str, start: str, end: str, res: dict, t: dict, note: str) -> dict:
    res["pass"] = bool(t["sharpe"] >= 0.5 and t["ci"][0] > 0)
    register({"trial": trial, "date": time.strftime("%Y-%m-%d"), "kind": "daytrade", "sharpe_ann": t["sharpe"],
              "window": f"{start}..{end}", "result": "pass" if res["pass"] else "fail"})
    print(f"{name} {trial}: Sharpe {t['sharpe']} CI{t['ci_level']:.1%} {t['ci']} CAGR {t['cagr']:.1%} "
          f"hit {t['hit_rate']:.0%} ({note}) -> {'PASS' if res['pass'] else 'FAIL'}", flush=True)
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rules", required=True, help="e.g. D3,D4")
    rules = ap.parse_args().rules.split(",")
    bars = ({s: pd.read_parquet(DATA / f"{s}_1min.parquet") for s in ("SPY", "QQQ")}
            if set(rules) - {"D5", "D7", "D8"} else {})
    core = pd.read_parquet(BACKEND / "results" / "planner" / "track_returns.parquet")["core"]
    f = BACKEND / "results" / "daytrade_test.json"
    out: dict = json.loads(f.read_text()) if f.exists() else {}
    for name in rules:
        if name in out:
            raise SystemExit(f"{name} was already run (one trial each); see {f.name}")
        if name == "D5":
            out[name] = run_d5(core)
            f.write_text(json.dumps(out, indent=1) + "\n")
            continue
        if name in ("D7", "D8"):
            out[name] = run_d7(core) if name == "D7" else run_d8(core)
            f.write_text(json.dumps(out, indent=1) + "\n")
            continue
        fn, start, trial = RULES[name]
        lv = LEVEL4 if name in ROUND4 else 0.95
        res: dict = {"window": [start, END]}
        for cost_bp in (1, 5):
            per = {s: fn(b, cost_bp / 1e4) for s, b in bars.items()}
            both = pd.concat(per, axis=1).fillna(0.0).mean(axis=1)  # equal capital; a symbol with no trade earns 0
            test = both.loc[start:END]
            res[f"{cost_bp}bp"] = {"both": stats(test, lv), **{s: stats(r.loc[start:END], lv) for s, r in per.items()}}
            if cost_bp == 1:
                res["before_window"] = stats(both.loc[:start].iloc[:-1]) if len(both.loc[:start]) > 30 else None
                res["corr_with_core"] = round(float(test.corr(core.reindex(test.index))), 3)
                if name == "D6":
                    res["since_2024_10"] = stats(both.loc["2024-10-01":END], lv)
        t = res["1bp"]["both"]
        res["pass"] = bool(t["sharpe"] >= 0.5 and t["ci"][0] > 0)
        register({"trial": trial, "date": time.strftime("%Y-%m-%d"), "kind": "daytrade", "sharpe_ann": t["sharpe"],
                  "window": f"{start}..{END}", "result": "pass" if res["pass"] else "fail"})
        out[name] = res
        print(f"{name} {trial}: Sharpe {t['sharpe']} CI {t['ci']} CAGR {t['cagr']:.1%} hit {t['hit_rate']:.0%} "
              f"(5 bps: Sharpe {res['5bp']['both']['sharpe']}) -> {'PASS' if res['pass'] else 'FAIL'}", flush=True)
        f.write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()
