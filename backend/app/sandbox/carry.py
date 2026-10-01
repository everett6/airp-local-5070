"""Crypto funding carry C1 (docs/PLAN_60_V2.md "Crypto funding carry C1"; Schmeling, Schrimpf and Todorov, "Crypto
Carry", BIS WP 1087, 2023): long spot, short the perpetual, collect its funding.

Per asset, each Monday 00:00 UTC: hold for the week if the mean hourly funding of the previous 168 hours is > 0 (with
at least 120 hours present), else flat. A held day earns the sum of that UTC day's hourly funding / (1 + margin); each
switch in or out costs `cost` on the week's first day. The sleeve is half BTC, half ETH.
"""
from __future__ import annotations

import pandas as pd

MIN_HOURS = 120


def asset_carry(r: pd.Series, cost: float, margin: float) -> tuple[pd.Series, pd.Series, pd.Series]:
    """One asset's daily (net, gross, held) from its hourly funding `r` (UTC DatetimeIndex)."""
    r = r.sort_index()
    ts = pd.DatetimeIndex(r.index)
    daily = r.groupby(ts.floor("D")).sum()
    first = ts.min().floor("D")
    mondays = pd.date_range(first - pd.Timedelta(days=first.weekday()), ts.max(), freq="7D")
    held_w, prev = {}, False
    cost_on = {}
    for w in mondays:
        past = r[(ts >= w - pd.Timedelta(hours=168)) & (ts < w)]
        h = bool(len(past) >= MIN_HOURS and past.mean() > 0)
        held_w[w] = h
        if h != prev:
            cost_on[w] = cost
        prev = h
    days = pd.DatetimeIndex(daily.index)
    week = days.floor("D") - pd.to_timedelta(days.weekday.to_numpy(), unit="D")
    held = pd.Series([held_w.get(w, False) for w in week], index=days)
    gross = (daily / (1 + margin)).where(held, 0.0)
    charge = pd.Series([cost_on.get(d, 0.0) if d.weekday() == 0 else 0.0 for d in days], index=days)
    return gross - charge, gross, held


def funding_carry(f: pd.DataFrame, cost: float = 0.0015, margin: float = 0.25) -> pd.DataFrame:
    """The sleeve's daily returns from hourly funding `f` (ts UTC, asset, rate): ret, gross, btc, eth, held_btc,
    held_eth; indexed by naive UTC date."""
    parts = {}
    for a in ("BTC", "ETH"):
        s = f[f["asset"] == a].set_index("ts")["rate"]
        net, gross, held = asset_carry(s, cost, margin)
        parts[a] = (net, gross, held)
    idx = parts["BTC"][0].index.union(parts["ETH"][0].index)
    out = pd.DataFrame(index=idx)
    for a, (net, gross, held) in parts.items():
        out[a.lower()] = net.reindex(idx).fillna(0.0)
        out[f"gross_{a.lower()}"] = gross.reindex(idx).fillna(0.0)
        out[f"held_{a.lower()}"] = held.reindex(idx).fillna(False).astype(bool)
    out["ret"] = 0.5 * out["btc"] + 0.5 * out["eth"]
    out["gross"] = 0.5 * out["gross_btc"] + 0.5 * out["gross_eth"]
    out.index = pd.DatetimeIndex(out.index).tz_localize(None)
    return out.drop(columns=["gross_btc", "gross_eth"])
