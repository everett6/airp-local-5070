"""The day-trading track's pre-registered test (docs/PLAN_60_V2.md "Day-trading track"): D1 and D2, one trial each.

    python scripts/daytrade_test.py --rules D3,D4   # round 2; needs data/intraday/{SPY,QQQ}_1min.parquet
    python scripts/daytrade_test.py --rules D6,D7,D8   # round 4 (98.3% CI: Bonferroni over its 3 trials)
    python scripts/daytrade_test.py --rules D9   # round 5 (then --check-d9)
    python scripts/daytrade_test.py --rules D10  # round 6; needs scripts/premarket_stocks.py, daily_alpaca.py split
    python scripts/daytrade_test.py --rules P1   # pairs trading (GGR), daily closes
    python scripts/daytrade_test.py --rules C1   # crypto funding carry; needs scripts/funding_data.py
    python scripts/daytrade_test.py --rules T1   # turn-of-the-month on SPY, daily closes
    python scripts/daytrade_test.py --rules O1   # SPY overnight premium, daily adjusted bars
    python scripts/daytrade_test.py --rules D12  # AI earnings day trade (the live judge's scores, daily bars)
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
    d9_open_reversal,
    d10_premarket_reversal,
    d11_loo_reversal,
    d12_ai_earnings,
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
    daily, names = daily_list()
    r = d8_darvas(daily, names, cost=0.001)
    test = r.loc[D8_START:D8_END]
    res: dict = {"window": [D8_START, D8_END], "excess": stats(test["excess"], LEVEL4),
                 "book": stats(test["ret"], LEVEL4), "spy": stats(test["spy"], LEVEL4),
                 "held_median": int(test["held"].median()),
                 "before_window_excess": stats(r.loc["2016-01-01":D8_START, "excess"].iloc[:-1], LEVEL4),
                 "corr_with_core": round(float(test["excess"].corr(core.reindex(test.index))), 3)}
    return finish("D8", D8_TRIAL, D8_START, D8_END, res, res["excess"], f"book CAGR {res['book']['cagr']:.1%}, "
                  f"SPY {res['spy']['cagr']:.1%}, median held {res['held_median']}")


D9_START, D9_END, D9_TRIAL = "2016-01-04", "2026-09-24", "daytrade_open_reversal"


def daily_list() -> tuple[pd.DataFrame, list[str]]:
    from intraday_stocks import universe

    daily = pd.read_parquet(BACKEND / "data" / "events" / "ohlcv_2009-01-01_2026-09-25.parquet")
    names = universe()
    daily = daily[(daily["Date"] >= "2014-01-01") & daily["Ticker"].isin(set(names) | {"SPY"})]
    return daily.assign(Date=lambda d: pd.to_datetime(d["Date"])), names


def run_d9(core: pd.Series) -> dict:
    """D9 (opening-auction reversal, daily bars of D5's list): one trial, 95% CI (single trial in round 5)."""
    daily, names = daily_list()
    res: dict = {"window": [D9_START, D9_END]}
    for bp in (1, 2):
        test = d9_open_reversal(daily, names, bp / 1e4).loc[D9_START:D9_END]
        res[f"{bp}bp"] = {"both": stats(test["ret"]), "long_leg_gross": round(float(test["long"].mean()) * 1e4, 2),
                          "short_leg_gross": round(float(test["short"].mean()) * 1e4, 2),
                          "names_median": int(test["names"].median())}
        if bp == 1:
            res["since_2024_07"] = stats(test.loc["2024-07-01":, "ret"])
            res["corr_with_core"] = round(float(test["ret"].corr(core.reindex(test.index))), 3)
    return finish("D9", D9_TRIAL, D9_START, D9_END, res, res["1bp"]["both"],
                  f"2 bps: Sharpe {res['2bp']['both']['sharpe']}")


D10_TRIAL = "daytrade_open_reversal_premarket"


def run_d10(core: pd.Series) -> dict:
    """D10 (D9 with a pre-market signal, tradable with MOO/MOC orders): one trial, 95% CI."""
    from intraday_stocks import universe

    daily = pd.read_parquet(DATA / "sp500_daily_alpaca_split.parquet")
    pre = pd.read_parquet(DATA / "sp500_premarket.parquet")
    names = universe()
    res: dict = {"window": [D9_START, D9_END]}
    for bp in (1, 2):
        test = d10_premarket_reversal(daily, pre, names, bp / 1e4).loc[D9_START:D9_END]
        res[f"{bp}bp"] = {"both": stats(test["ret"]), "long_leg_gross": round(float(test["long"].mean()) * 1e4, 2),
                          "short_leg_gross": round(float(test["short"].mean()) * 1e4, 2),
                          "names_median": int(test["names"].median())}
        if bp == 1:
            res["since_2024_07"] = stats(test.loc["2024-07-01":, "ret"])
            res["corr_with_core"] = round(float(test["ret"].corr(core.reindex(test.index))), 3)
    return finish("D10", D10_TRIAL, D9_START, D9_END, res, res["1bp"]["both"],
                  f"2 bps: Sharpe {res['2bp']['both']['sharpe']}, median {res['1bp']['names_median']} stocks")


D11_TRIAL, D11_LEVEL = "daytrade_open_reversal_loo", 1 - 0.05 / 3  # third trial of the opening-reversal family


def run_d11(core: pd.Series) -> dict:
    """D11 (D9's selection made with limit-on-open orders placed from pre-market prices): one trial, 98.3% CI."""
    from intraday_stocks import universe

    daily = pd.read_parquet(DATA / "sp500_daily_alpaca_split.parquet")
    pre = pd.read_parquet(DATA / "sp500_premarket.parquet")
    names = universe()
    spy = daily[daily["Ticker"] == "SPY"].set_index("Date").sort_index()
    spy_oc = spy["Close"] / spy["Open"] - 1
    spy_oc.index = pd.to_datetime(spy_oc.index)
    res: dict = {"window": [D9_START, D9_END]}
    for bp in (1, 2):
        test = d11_loo_reversal(daily, pre, names, bp / 1e4).loc[D9_START:D9_END]
        res[f"{bp}bp"] = {"both": stats(test["ret"], level=D11_LEVEL), "ci95": list(stats(test["ret"])["ci"]),
                          "long_side": stats(test["long"]), "short_side": stats(test["short"])}
        if bp == 1:
            one_sided = (test["n_long"] > 0) != (test["n_short"] > 0)
            res["since_2024_07"] = stats(test.loc["2024-07-01":, "ret"])
            res["fills_per_day"] = {"long": round(float(test["n_long"].mean()), 1),
                                    "short": round(float(test["n_short"].mean()), 1)}
            res["gross_mean"] = round(float(test["gross"].mean()), 3)
            res["net_mean"] = round(float(test["net"].mean()), 3)
            res["net_abs_mean"] = round(float(test["net"].abs().mean()), 3)
            res["one_sided_days"] = round(float(one_sided.mean()), 3)
            res["no_fill_days"] = round(float(((test["n_long"] + test["n_short"]) == 0).mean()), 3)
            res["resting_orders_x_capital"] = round(float(test.loc[test["orders"] > 0, "orders"].mean()), 2)
            res["names_median"] = int(test["names"].median())
            res["corr_with_core"] = round(float(test["ret"].corr(core.reindex(test.index))), 3)
            res["corr_with_spy_open_to_close"] = round(float(test["ret"].corr(spy_oc.reindex(test.index))), 3)
    return finish("D11", D11_TRIAL, D9_START, D9_END, res, res["1bp"]["both"],
                  f"2 bps: Sharpe {res['2bp']['both']['sharpe']}, fills a day {res['fills_per_day']}, "
                  f"gross {res['gross_mean']}")


P1_START, P1_END, P1_TRIAL = "2024-07-01", "2026-09-24", "pairs_ggr"


def run_p1(core: pd.Series) -> dict:
    """Pairs trading P1 (GGR within sectors, daily closes of D5's list): one trial, 95% CI."""
    from app.sandbox.pairs import pairs_ggr

    daily, names = daily_list()
    closes = daily.pivot_table(index="Date", columns="Ticker", values="Close")[[n for n in names if n in set(daily["Ticker"])]]
    ev = pd.read_csv(BACKEND / "data" / "events" / "events_2024-01-01_2026-09-24.csv")
    sectors = ev.drop_duplicates("ticker").set_index("ticker")["sector"].dropna().to_dict()
    res: dict = {"window": [P1_START, P1_END]}
    for bp in (10, 0, 20):
        r = pairs_ggr(closes.loc["2015-01-01":], sectors, cost=bp / 1e4)
        test = r.loc[P1_START:P1_END]
        res[f"{bp}bp"] = stats(test["ret"])
        if bp == 10:
            res["before_window"] = stats(r.loc["2016-01-01":P1_START, "ret"].iloc[:-1])
            res["invested_share"] = round(float(test["invested"].mean()), 3)
            res["corr_with_core"] = round(float(test["ret"].corr(core.reindex(test.index))), 3)
    return finish("P1", P1_TRIAL, P1_START, P1_END, res, res["10bp"],
                  f"0 bps {res['0bp']['sharpe']}, 20 bps {res['20bp']['sharpe']}, invested {res['invested_share']:.0%}")


C1_START, C1_END, C1_TRIAL = "2023-05-01", "2026-09-24", "crypto_funding_carry"


def run_c1(core: pd.Series) -> dict:
    """C1 crypto funding carry (Deribit funding; excess over the 3-month T-bill): one trial, 95% CI."""
    from vol_target_b0 import tbill

    from app.sandbox.carry import funding_carry
    r = funding_carry(pd.read_parquet(BACKEND / "data" / "crypto" / "funding_deribit.parquet"))
    rf = tbill().reindex(r.index, method="ffill").fillna(0.0) / 365  # crypto trades every day
    ex = (r["ret"] - rf).rename("ex")
    test, full = ex.loc[C1_START:C1_END], r.loc[C1_START:C1_END]
    k = (365 / 252) ** 0.5  # stats() annualizes with 252 trading days; crypto earns all 365

    def st365(x: pd.Series) -> dict:
        d = stats(x)
        return d | {"sharpe": round(d["sharpe"] * k, 3), "ci": [round(c * k, 3) for c in d["ci"]]}
    res: dict = {"window": [C1_START, C1_END], "excess": st365(test), "raw": st365(full["ret"]),
                 "gross_cagr": round(float((1 + full["gross"]).prod() ** (365 / len(full)) - 1), 4),
                 "held_share": {a: round(float(full[f"held_{a}"].mean()), 3) for a in ("btc", "eth")},
                 "by_year_excess_sharpe": {str(y): round(sharpe(g) * k, 2) for y, g in test.groupby(test.index.year)},
                 "by_asset_cagr": {a: round(float((1 + full[a]).prod() ** (365 / len(full)) - 1), 4)
                                   for a in ("btc", "eth")},
                 "before_window_excess": st365(ex.loc[:C1_START].iloc[:-1]) if len(ex.loc[:C1_START]) > 60 else None,
                 "corr_with_core": round(float(test.corr(core.reindex(test.index))), 3),
                 "note": "Sharpe annualized with sqrt(365): daily returns on every calendar day"}
    return finish("C1", C1_TRIAL, C1_START, C1_END, res, res["excess"],
                  f"raw CAGR {res['raw']['cagr']:.1%}, held BTC {res['held_share']['btc']:.0%} ETH "
                  f"{res['held_share']['eth']:.0%}")


T1_START, T1_END, T1_TRIAL = "2008-01-02", "2026-09-25", "turn_of_month"


def run_t1(core: pd.Series) -> dict:
    """T1 turn-of-the-month on SPY (excess over the 3-month T-bill): overlay rule + TOM-minus-other difference."""
    from vol_target_b0 import tbill

    from app.sandbox.calendar_fx import diff_ci, tom_overlay
    spy = pd.read_parquet(BACKEND / "data" / "trend" / "etf_closes.parquet")["SPY"].dropna()
    rf = tbill().reindex(spy.index, method="ffill").fillna(0.0) / 252
    ex = (spy.pct_change() - rf).dropna()
    res: dict = {"window": [T1_START, T1_END]}
    for bp in (1, 3):
        o = tom_overlay(ex, bp / 1e4).loc[T1_START:T1_END]
        res[f"{bp}bp"] = stats(o["ret"])
    o = tom_overlay(ex, 1e-4).loc[T1_START:T1_END]
    x, tom = ex.loc[T1_START:T1_END], o["tom"]
    point, lo, hi = diff_ci(x, tom)
    res["diff_bp_per_day"] = {"point": round(point * 1e4, 2), "ci": [round(lo * 1e4, 2), round(hi * 1e4, 2)],
                              "tom_mean": round(float(x[tom].mean()) * 1e4, 2),
                              "other_mean": round(float(x[~tom].mean()) * 1e4, 2), "tom_days": int(tom.sum())}
    res["by_year"] = {str(y): {"sharpe": round(sharpe(g["ret"]), 2),
                               "diff_bp": round(float(x.loc[g.index][g["tom"]].mean()
                                                      - x.loc[g.index][~g["tom"]].mean()) * 1e4, 1)}
                      for y, g in o.groupby(o.index.year)}
    late = o.loc["2020-07-01":]
    lp, llo, lhi = diff_ci(x.loc[late.index], late["tom"])
    res["since_2020_07"] = {"overlay": stats(late["ret"]),
                            "diff_bp": [round(lp * 1e4, 2), round(llo * 1e4, 2), round(lhi * 1e4, 2)]}
    timed = o["ret"] + rf.reindex(o.index)  # SPY on TOM days, T-bills otherwise (1 bp a side)
    spy_r = spy.pct_change().loc[T1_START:T1_END]
    res["timed_vs_spy"] = {"timed": stats(timed), "spy": stats(spy_r)}
    res["corr_with_core"] = round(float(o["ret"].corr(core.reindex(o.index))), 3)
    t = res["1bp"]
    res["pass"] = bool(t["sharpe"] >= 0.5 and t["ci"][0] > 0 and lo > 0)
    register({"trial": T1_TRIAL, "date": time.strftime("%Y-%m-%d"), "kind": "calendar", "sharpe_ann": t["sharpe"],
              "window": f"{T1_START}..{T1_END}", "result": "pass" if res["pass"] else "fail"})
    d = res["diff_bp_per_day"]
    print(f"T1 {T1_TRIAL}: overlay Sharpe {t['sharpe']} CI {t['ci']} CAGR {t['cagr']:.1%}; TOM-minus-other "
          f"{d['point']} bp/day CI {d['ci']} -> {'PASS' if res['pass'] else 'FAIL'}", flush=True)
    return res


O1_START, O1_END, O1_TRIAL = "2012-01-03", "2026-09-25", "spy_overnight"


def run_o1(core: pd.Series) -> dict:
    """O1 SPY overnight premium, one pre-registered calendar trial."""
    from vol_target_b0 import tbill

    from app.sandbox.overnight import data_check, overnight_excess, overnight_legs

    prices = pd.read_parquet(BACKEND / "data" / "statarb" / "sector_etfs.parquet")
    opens = prices[("SPY", "Open")]
    closes = prices[("SPY", "Close")]
    legs = overnight_legs(opens, closes)
    rf = tbill().reindex(legs.index, method="ffill").fillna(0.0) / 252
    daily = pd.DataFrame({"Open": opens, "Close": closes}).loc["2016-01-01":O1_END]
    minute = pd.read_parquet(DATA / "SPY_1min.parquet")
    ts = pd.to_datetime(minute["ts"])
    minute = minute.loc[(ts.dt.date >= pd.Timestamp("2016-01-01").date()) &
                        (ts.dt.date <= pd.Timestamp(O1_END).date())]
    check = data_check(daily, minute)
    if check["share_ok"] < 0.99:
        raise SystemExit(f"O1 daily/minute data check failed: {check}")

    excess_1bp = overnight_excess(legs, rf, 1e-4)
    excess_3bp = overnight_excess(legs, rf, 3e-4)
    window = slice(O1_START, O1_END)
    res: dict = {"window": [O1_START, O1_END],
                 "1bp": stats(excess_1bp.loc[window]), "3bp": stats(excess_3bp.loc[window]),
                 "gross": stats((legs["overnight"] - rf).loc[window]),
                 "intraday_gross": stats((legs["intraday"] - rf).loc[window]),
                 "buy_hold": stats((legs["full"] - rf).loc[window]),
                 "by_year": {str(year): round(sharpe(group), 2)
                             for year, group in excess_1bp.loc[window].groupby(
                                 excess_1bp.loc[window].index.year)},
                 "since_2020": stats(excess_1bp.loc["2020-01-01":O1_END]),
                 "corr_with_core": round(float(excess_1bp.loc[window].corr(
                     core.reindex(excess_1bp.loc[window].index))), 3),
                 "data_check": check}
    result = res["1bp"]
    res["pass"] = bool(result["sharpe"] >= 0.5 and result["ci"][0] > 0)
    register({"trial": O1_TRIAL, "date": time.strftime("%Y-%m-%d"), "kind": "calendar",
              "sharpe_ann": result["sharpe"], "window": f"{O1_START}..{O1_END}",
              "result": "pass" if res["pass"] else "fail"})
    print(f"O1 {O1_TRIAL}: Sharpe {result['sharpe']} CI {result['ci']} CAGR {result['cagr']:.1%} "
          f"(3 bps: Sharpe {res['3bp']['sharpe']}) -> {'PASS' if res['pass'] else 'FAIL'}", flush=True)
    return res


E1_START, E1_END, E1_TRIAL = "2013-05-01", "2026-09-25", "macro_announcement"


def run_e1(core: pd.Series) -> dict:
    """E1 scheduled macro-announcement overlay; call only for its one registered run."""
    from vol_target_b0 import tbill

    from app.sandbox.calendar_fx import diff_ci
    from app.sandbox.macro_events import announcement_strategy, event_days
    from scripts.macro_calendar import count_exceptions

    calendar_path = BACKEND / "data" / "macro" / "announcements.csv"
    calendar = pd.read_csv(calendar_path, dtype=str)
    problems = count_exceptions(calendar)
    if problems:
        raise SystemExit("E1 calendar count check failed: " + "; ".join(problems))
    spy = pd.read_parquet(BACKEND / "data" / "trend" / "etf_closes.parquet")["SPY"].dropna()
    rf = tbill().reindex(spy.index, method="ffill").fillna(0.0) / 252
    ex = (spy.pct_change() - rf).dropna()
    window = slice(E1_START, E1_END)
    x = ex.loc[window]
    calendar_dates = pd.to_datetime(calendar["date"])
    in_window = (calendar_dates >= pd.Timestamp(E1_START)) & (calendar_dates <= pd.Timestamp(E1_END))
    window_calendar = calendar.loc[in_window]
    event_series = event_days(pd.DatetimeIndex(x.index), window_calendar["date"].tolist())
    missing = sorted(set(window_calendar["date"]) - set(x.index.strftime("%Y-%m-%d")))
    if missing:
        print(f"E1 calendar dates outside trading index (not mapped): {missing}", flush=True)
    res: dict = {"window": [E1_START, E1_END]}
    overlays: dict[int, pd.DataFrame] = {}
    for bp in (1, 3):
        overlays[bp] = announcement_strategy(x, event_series, bp / 1e4)
        res[f"{bp}bp"] = stats(overlays[bp]["ret"])
    point, lo, hi = diff_ci(x, event_series, block=21, n=5000, seed=0, level=0.95)
    res["diff_bp_per_day"] = {"point": round(point * 1e4, 2), "ci": [round(lo * 1e4, 2), round(hi * 1e4, 2)],
                              "event_mean": round(float(x[event_series].mean()) * 1e4, 2),
                              "other_mean": round(float(x[~event_series].mean()) * 1e4, 2),
                              "event_days": int(event_series.sum())}
    res["by_event"] = {}
    for kind in ("jobs", "ppi", "fomc"):
        mask = event_days(pd.DatetimeIndex(x.index),
                          window_calendar.loc[window_calendar.event == kind, "date"].tolist())
        res["by_event"][kind] = stats(announcement_strategy(x, mask, 1e-4)["ret"])
    one = overlays[1]["ret"]
    res["by_year"] = {str(year): round(sharpe(group), 2) for year, group in one.groupby(one.index.year)}
    res["corr_with_core"] = round(float(one.corr(core.reindex(one.index))), 3)
    t = res["1bp"]
    res["pass"] = bool(t["sharpe"] >= 0.5 and t["ci"][0] > 0 and lo > 0)
    register({"trial": E1_TRIAL, "date": time.strftime("%Y-%m-%d"), "kind": "calendar",
              "sharpe_ann": t["sharpe"], "window": f"{E1_START}..{E1_END}",
              "result": "pass" if res["pass"] else "fail"})
    print(f"E1 {E1_TRIAL}: Sharpe {t['sharpe']} CI {t['ci']}; event-minus-other {res['diff_bp_per_day']['point']} "
          f"bp/day CI {res['diff_bp_per_day']['ci']} -> {'PASS' if res['pass'] else 'FAIL'}", flush=True)
    return res


def check_d9() -> dict:
    """D9's pre-stated validity check: the same rule on Alpaca's SIP daily bars (scripts/daily_alpaca.py)."""
    from intraday_stocks import universe

    daily = pd.read_parquet(DATA / "sp500_daily_alpaca.parquet")
    names = universe()
    test = d9_open_reversal(daily, names, 1e-4).loc[D9_START:D9_END]
    trimmed = d9_open_reversal(daily, names, 1e-4, trim=0.01).loc[D9_START:D9_END]
    t = stats(test["ret"])
    res = {"source": "alpaca_sip_daily_adjusted", "1bp": t, "long_leg_gross": round(float(test["long"].mean()) * 1e4, 2),
           "short_leg_gross": round(float(test["short"].mean()) * 1e4, 2), "trimmed_1pct": stats(trimmed["ret"]),
           "since_2024_07": stats(test.loc["2024-07-01":, "ret"]), "pass": bool(t["sharpe"] >= 0.5 and t["ci"][0] > 0)}
    print(f"D9 validity (Alpaca): Sharpe {t['sharpe']} CI {t['ci']} CAGR {t['cagr']:.1%}; trimmed "
          f"{res['trimmed_1pct']['sharpe']} -> {'HOLDS' if res['pass'] else 'FAILS'}", flush=True)
    return res


D12_TRIAL = "daytrade_ai_earnings"


def run_d12(core: pd.Series) -> dict:
    """D12 (PLAN_60_V2 "Day-trading round 8"): the live judge's fact-sheet-v2 scores of the 3,175 sample releases,
    each release's entry day traded open to close on the score's side, hedged with SPY."""
    from app.forward.schedule import entry_session
    ev = BACKEND / "results" / "events"
    judge = pd.concat([pd.read_json(ev / f, lines=True) for f in ("decide_bonsai-27b_latest_factsheet2024_v2_h5.jsonl",
                                                                 "decide_bonsai-27b_latest_factsheet_v2_h5.jsonl")])
    judge = judge[~judge["censored"].astype(bool)].drop_duplicates("accession")
    events = pd.concat([pd.read_csv(BACKEND / "data" / "events" / f) for f in ("events_sp500_2024.csv", "events_sp500_2025.csv")])
    rel = judge[["accession", "logodds"]].merge(events[["accession", "ticker", "accepted_utc"]].drop_duplicates("accession"),
                                                on="accession")
    rel["accepted_utc"] = pd.to_datetime(rel["accepted_utc"], utc=True)
    rel["ticker"] = rel["ticker"].str.replace(".", "-", regex=False)  # the price file spells class shares BRK-B
    px = pd.read_parquet(BACKEND / "data" / "events" / "ohlcv_2023-01-01_2026-09-25.parquet")
    px["Date"] = pd.to_datetime(px["Date"])
    opens = px.pivot_table(index="Date", columns="Ticker", values="Open").loc[:"2026-09-24"]
    closes = px.pivot_table(index="Date", columns="Ticker", values="Close").loc[:"2026-09-24"]
    res: dict = {"scored_releases": len(judge), "matched_releases": len(rel)}
    for bp in (12, 22):
        daily, trades, counts = d12_ai_earnings(rel, opens, closes, entry_session, bp / 1e4)
        part = {"book": stats(daily), "trades": len(trades), "counts": counts,
                "trades_per_trading_day": round(float(trades.groupby("day").size().mean()), 2),
                "trade_hit_rate": round(float((trades["ret"] > 0).mean()), 3),
                "mean_trade_bp": round(float(trades["ret"].mean() * 1e4), 2)}
        for name, side in (("long", 1), ("short", -1)):
            t = trades[trades["side"] == side]
            part[name] = {"trades": len(t), "mean_trade_bp": round(float(t["ret"].mean() * 1e4), 2),
                          "book": stats(t.groupby("day")["ret"].mean().reindex(daily.index, fill_value=0.0))}
        part["by_year"] = {str(y): stats(g) for y, g in daily.groupby(daily.index.year) if len(g) > 40}
        spy_oc = closes["SPY"] / opens["SPY"] - 1
        part["corr_spy_open_close"] = round(float(daily.corr(spy_oc.reindex(daily.index))), 3)
        part["corr_core"] = round(float(daily.corr(core.reindex(daily.index))), 3)
        res[f"{bp}bp"] = part
    udaily, _, _ = d12_ai_earnings(rel, opens, closes, entry_session, 10 / 1e4, hedge=False)
    res["unhedged_10bp"] = stats(udaily)
    t = res["12bp"]["book"]
    res["window"] = [str(udaily.index.min().date()), "2026-09-24"]
    return finish("D12", D12_TRIAL, res["window"][0], res["window"][1], res, t,
                  f"{res['12bp']['trades']} trades, {res['12bp']['mean_trade_bp']} bp a trade net")


def finish(name: str, trial: str, start: str, end: str, res: dict, t: dict, note: str) -> dict:
    res["pass"] = bool(t["sharpe"] >= 0.5 and t["ci"][0] > 0)
    register({"trial": trial, "date": time.strftime("%Y-%m-%d"), "kind": "daytrade", "sharpe_ann": t["sharpe"],
              "window": f"{start}..{end}", "result": "pass" if res["pass"] else "fail"})
    print(f"{name} {trial}: Sharpe {t['sharpe']} CI{t['ci_level']:.1%} {t['ci']} CAGR {t['cagr']:.1%} "
          f"hit {t['hit_rate']:.0%} ({note}) -> {'PASS' if res['pass'] else 'FAIL'}", flush=True)
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rules", default="", help="e.g. D3,D4")
    ap.add_argument("--check-d9", action="store_true", help="D9's pre-stated validity check (run once)")
    a = ap.parse_args()
    if a.check_d9:
        f = BACKEND / "results" / "daytrade_test.json"
        out = json.loads(f.read_text())
        if "validity" in out["D9"]:
            raise SystemExit("the D9 validity check was already run")
        out["D9"]["validity"] = check_d9()
        f.write_text(json.dumps(out, indent=1) + "\n")
        return
    rules = a.rules.split(",")
    bars = ({s: pd.read_parquet(DATA / f"{s}_1min.parquet") for s in ("SPY", "QQQ")}
            if set(rules) - {"D5", "D7", "D8", "D9", "D10", "D11", "D12", "P1", "C1", "T1", "O1", "E1"} else {})
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
        if name in ("D7", "D8", "D9", "D10", "D11", "D12", "P1", "C1", "T1", "O1", "E1"):
            out[name] = {"D7": run_d7, "D8": run_d8, "D9": run_d9, "D10": run_d10, "D11": run_d11, "D12": run_d12, "P1": run_p1,
                         "C1": run_c1, "T1": run_t1, "O1": run_o1, "E1": run_e1}[name](core)
            f.write_text(json.dumps(out, indent=1) + "\n")
            continue
        fn, start, trial = RULES[name]
        lv = LEVEL4 if name in ROUND4 else 0.95
        res: dict = {"window": [start, END]}
        for cost_bp in (1, 5):
            per = {s: fn(b, cost_bp / 1e4) for s, b in bars.items()}
            both = pd.concat(per, axis=1, sort=True).fillna(0.0).mean(axis=1)  # equal capital; a symbol with no trade earns 0
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
