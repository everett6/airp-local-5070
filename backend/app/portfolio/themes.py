"""Investment themes and the market-risk register (docs/PLAN_60_V2.md "Theme track", fixed 2026-09-28).

The user asked the AI to weigh investment directions by horizon (long-term: biotech, quantum computing...; medium:
hyperscalers...) and risks such as an AI bubble. Code fixes the theme list, the price proxy of each theme and every
number on the cards; Bonsai only rates (scripts/themes.py). Forward-only: Bonsai knows how 2024-26 went.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Theme:
    key: str
    label: str
    horizon: str  # "medium" (held 6 months) or "long" (held 12 months)
    proxy: tuple[str, ...]  # one ETF, or an equal-weight basket of stocks
    about: str
    ai_linked: bool = False  # capped when the AI-bubble gauge reads "high"


THEMES = [
    Theme("hyperscalers", "Hyperscalers", "medium", ("MSFT", "AMZN", "GOOGL", "META", "ORCL"),
          "cloud and AI-infrastructure spenders (equal weight)", ai_linked=True),
    Theme("semis", "Semiconductors", "medium", ("SMH",), "chip designers, foundries and equipment makers",
          ai_linked=True),
    Theme("power_grid", "Power and grid", "medium", ("GRID",), "electrical grid, power equipment and transmission",
          ai_linked=True),
    Theme("software", "Software", "medium", ("IGV",), "application and infrastructure software (AI helps or undercuts)"),
    Theme("cyber", "Cybersecurity", "medium", ("CIBR",), "network and cloud security"),
    Theme("utilities", "Utilities", "medium", ("XLU",), "regulated power and gas utilities"),
    Theme("biotech", "Biotech", "long", ("XBI",), "small and mid-cap biotechnology (equal weight)"),
    Theme("quantum", "Quantum computing", "long", ("QTUM",), "quantum computing and machine-learning hardware"),
    Theme("nuclear", "Nuclear energy", "long", ("NLR",), "uranium, reactors and nuclear utilities"),
    Theme("robotics", "Robotics and automation", "long", ("BOTZ",), "industrial robots, automation and AI hardware"),
    Theme("space", "Space", "long", ("UFO",), "satellites, launch and space services"),
    Theme("solar", "Solar", "long", ("TAN",), "solar manufacturers and developers"),
    Theme("batteries", "Batteries and lithium", "long", ("LIT",), "lithium miners and battery makers"),
]
HOLD = {"medium": 126, "long": 252}  # trading days
N_PICKS, MIN_RATING, COST = 2, 4, 0.001  # per horizon; ETF cost per side
HYPERSCALER_CIKS = {"MSFT": 789019, "AMZN": 1018724, "GOOGL": 1652044, "META": 1326801, "ORCL": 1341439}
CAPEX_TAGS = ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets")


def tickers() -> set[str]:
    return {t for th in THEMES for t in th.proxy} | {"SPY", "RSP"}


def proxy_series(closes: pd.DataFrame, th: Theme) -> pd.Series:
    """The theme's price: the ETF, or an equal-weight basket rebalanced daily (mean of daily returns)."""
    px = closes[[t for t in th.proxy if t in closes]].ffill()
    r = px.pct_change().mean(axis=1, skipna=True).fillna(0.0)
    return (1 + r).cumprod().where(px.notna().any(axis=1))


def stats(s: pd.Series, spy: pd.Series) -> dict[str, Any]:
    """Returns vs SPY over 1/3/12 months, 12-1 momentum, drawdown from the 12-month high, distance from the 200-day
    average and 12-month volatility, from closes dated before today."""
    s, spy = s.dropna(), spy.reindex(s.dropna().index).ffill()

    def rel(n: int) -> float | None:
        return float(s.iloc[-1] / s.iloc[-n - 1] - spy.iloc[-1] / spy.iloc[-n - 1]) if len(s) > n else None
    out: dict[str, Any] = {"r1": rel(21), "r3": rel(63), "r12": rel(252)}
    out["mom"] = (float(s.iloc[-22] / s.iloc[-253] - spy.iloc[-22] / spy.iloc[-253]) if len(s) > 253 else None)
    last = s.iloc[-252:]
    out["dd"] = float(s.iloc[-1] / last.max() - 1)
    out["vs200"] = float(s.iloc[-1] / s.iloc[-200:].mean() - 1) if len(s) >= 200 else None
    out["vol"] = float(s.pct_change().iloc[-252:].std() * np.sqrt(252))
    return out


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.1%}"


def card(th: Theme, st: dict[str, Any], risk_lines: list[str]) -> str:
    hold = "6 months" if th.horizon == "medium" else "12 months"
    return (f"Theme: {th.label} ({th.about})\nHolding period if picked: {hold}\n"
            f"Price proxy: {', '.join(th.proxy)}\n"
            f"1-month return vs S&P 500: {_pct(st['r1'])}\n3-month return vs S&P 500: {_pct(st['r3'])}\n"
            f"12-month return vs S&P 500: {_pct(st['r12'])}\n"
            f"Drawdown from its 12-month high: {st['dd']:.1%}\n"
            f"Price vs its 200-day average: {_pct(st['vs200'])}\n"
            f"Annualized volatility (12 months): {st['vol']:.0%}\n\n"
            "=== Market risk register ===\n" + "\n".join(risk_lines))


def quarters(entries: list[dict[str, Any]]) -> pd.Series:
    """Quarterly values of a cash-flow fact from SEC companyfacts entries (10-Q year-to-date and 10-K annual
    durations), by quarter end: a 3-month duration is used as is; a longer one minus the same-start duration that
    ends one quarter earlier."""
    by: dict[tuple[str, str], float] = {}
    for x in entries:
        if x.get("form") in ("10-Q", "10-K") and x.get("start") and x.get("end"):
            by[(x["start"], x["end"])] = float(x["val"])
    q: dict[pd.Timestamp, float] = {}
    for (s, e), v in by.items():
        if 80 <= (pd.Timestamp(e) - pd.Timestamp(s)).days <= 100:
            q[pd.Timestamp(e)] = v
    for (s, e), v in by.items():
        if (pd.Timestamp(e) - pd.Timestamp(s)).days > 100 and pd.Timestamp(e) not in q:
            prev = [(pe, pv) for (ps, pe), pv in by.items()
                    if ps == s and 60 <= (pd.Timestamp(e) - pd.Timestamp(pe)).days <= 100]
            if prev:
                q[pd.Timestamp(e)] = v - max(prev)[1]
    return pd.Series(q, dtype=float).sort_index()


def ttm(q: pd.Series, lag_quarters: int = 0) -> float | None:
    """Trailing four quarters ending `lag_quarters` quarters before the latest; None without 4 consecutive ones."""
    if len(q) < 4 + lag_quarters:
        return None
    w = q.iloc[len(q) - 4 - lag_quarters: len(q) - lag_quarters]
    gaps = np.diff(w.index.values).astype("timedelta64[D]").astype(int)
    return float(w.sum()) if (gaps < 100).all() else None


def risk_lines(capex_now: float | None, capex_ago: float | None, ocf_now: float | None, spy_rsp_12m: float | None,
               semis: dict[str, Any], hy_now: float | None, hy_3m: float | None, vix: float | None) -> list[str]:
    """The AI-bubble / market-risk register as card lines, every number computed by code."""
    g = None if not capex_now or not capex_ago else capex_now / capex_ago - 1
    return [
        "Hyperscaler capex (MSFT, AMZN, GOOGL, META, ORCL), last 4 quarters: "
        + ("n/a" if capex_now is None else f"${capex_now / 1e9:,.0f} billion") + f", growth vs a year earlier: {_pct(g)}",
        "Hyperscaler capex as a share of their operating cash flow: "
        + ("n/a" if not capex_now or not ocf_now else f"{capex_now / ocf_now:.0%}"),
        f"Market concentration: S&P 500 (cap-weighted) minus equal-weight S&P 500, 12 months: {_pct(spy_rsp_12m)}",
        (f"Semiconductors (SMH): 12-month return vs S&P 500 {_pct(semis.get('r12'))}, "
         f"price vs 200-day average {_pct(semis.get('vs200'))}"),
        "High-yield credit spread: " + ("n/a" if hy_now is None else f"{hy_now:.2f}%")
        + (", change over 3 months: n/a" if hy_3m is None else f", change over 3 months: {hy_3m:+.2f} points"),
        "VIX: " + ("n/a" if vix is None else f"{vix:.1f}"),
    ]


def choose(rated: list[dict[str, Any]], horizon: str, bubble: str) -> list[str]:
    """Up to 2 themes of the horizon rated 4 or 5 (ties: better 12-1 momentum); AI-linked themes are left out when
    the bubble gauge reads high."""
    ok = [r for r in rated if r["horizon"] == horizon and r["rating"] >= MIN_RATING
          and not (bubble == "high" and r["ai_linked"])]
    ok.sort(key=lambda r: (-r["rating"], -(r["mom"] if r["mom"] is not None else -9)))
    return [r["key"] for r in ok[:N_PICKS]]


def momentum_baseline(rated: list[dict[str, Any]], horizon: str) -> list[str]:
    """The code-only yardstick: the 2 themes of the horizon with the best 12-1 month momentum vs SPY."""
    rs = [r for r in rated if r["horizon"] == horizon and r["mom"] is not None]
    return [r["key"] for r in sorted(rs, key=lambda r: -r["mom"])[:N_PICKS]]


def basket_return(opens: pd.DataFrame, keys: list[str], i: int, j: int) -> float:
    """Equal weight over the chosen themes (each theme equal weight over its proxy), entry open i to exit open j.
    No theme -> 0 excess (the money stays in SPY)."""
    by_key = {th.key: th for th in THEMES}
    rets = []
    for k in keys:
        px = [float(opens[t].iloc[j] / opens[t].iloc[i] - 1) for t in by_key[k].proxy
              if t in opens and pd.notna(opens[t].iloc[i]) and pd.notna(opens[t].iloc[j])]
        if px:
            rets.append(float(np.mean(px)))
    return float(np.mean(rets)) if rets else float(opens["SPY"].iloc[j] / opens["SPY"].iloc[i] - 1)


def score(recs: list[dict[str, Any]], opens: pd.DataFrame) -> list[dict[str, Any]]:
    """Results for each cohort x horizon whose exit open is in the data and not yet scored: the picks' and the
    momentum baseline's excess vs SPY, after costs (costs only when something was bought)."""
    done = {(r["month"], r["horizon"]) for r in recs if r.get("type") == "result"}
    days = pd.DatetimeIndex(opens.index)
    out = []
    for c in recs:
        if c.get("type") != "cohort":
            continue
        i = int(days.searchsorted(pd.Timestamp(c["made_on"]) + pd.Timedelta(days=1)))
        for h, hold in HOLD.items():
            if (c["month"], h) in done or i + hold >= len(days):
                continue
            spy = float(opens["SPY"].iloc[i + hold] / opens["SPY"].iloc[i] - 1)
            picks, base = c["picks"][h], c["baseline"][h]
            ex = basket_return(opens, picks, i, i + hold) - spy - (2 * COST if picks else 0.0)
            bx = basket_return(opens, base, i, i + hold) - spy - 2 * COST
            out.append({"month": c["month"], "horizon": h, "entry": days[i].date().isoformat(),
                        "exit": days[i + hold].date().isoformat(), "picks": picks, "excess_net": round(ex, 5),
                        "baseline_excess_net": round(bx, 5)})
    return out
