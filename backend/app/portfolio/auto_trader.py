"""Full autopilot (docs/FULL_AUTOPILOT.md): the research agents' horizon ratings become long and short positions in a
SEPARATE Alpaca paper account. User-requested on 4 Oct 2026 as an unvalidated paper experiment: it is not a
registered trial, counts toward no go-live gate and never touches the main paper account or the frozen books.

Pure functions only (no network), so every rule is tested on plain data:
- a rating of 4/5 is a bull call (long), 2/1 a bear call (short), 3 is PASS (nothing); 5 and 1 get double weight;
- the market regime (QQQ above or below its 50-day average) halves the trades that go against it;
- day calls are held for one session and flattened before the close; short/medium/long calls for 5/21/63 sessions;
- per-name and gross caps scale the book down, never up; whole shares only (Alpaca cannot short fractions);
- a long/short flip is sent as two steps: close first, open the other side on a later cycle.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.forward.schedule import is_session

HOLD = {"day": 0, "short": 5, "medium": 21, "long": 63}  # sessions held; 0 = flat by the close of the entry session


@dataclass(frozen=True)
class Config:
    per_name: float = 0.10       # largest |weight| one symbol may have across horizons
    gross: float = 2.0           # largest sum of |weights| (Reg T overnight limit)
    base: dict[str, float] = field(default_factory=lambda: {"day": 0.05, "short": 0.04, "medium": 0.04, "long": 0.04})
    against_trend: float = 0.5   # weight multiplier for a call against the market regime
    day_stop: float = 0.03       # a day trade 3% against its entry is closed
    swing_stop: float = 0.10     # a held trade 10% against its entry is closed
    max_drawdown: float = 0.35   # equity 35% below the starting equity: everything is closed and trading stops
    max_age_h: float = 36.0      # research older than this opens nothing new
    day_entry_cutoff: str = "14:30"  # no new day trade after this New York time
    flatten_at: str = "15:50"    # day trades are closed from this New York time
    min_notional: float = 50.0
    # sizing rules (user, 4 Oct 2026): code, not the model, decides how much
    risk_per_trade: float = 0.01  # a position may lose at most ~1% of equity at a 1.5-ATR move over its holding period
    stop_atr: float = 1.5
    support: dict[str, float] = field(default_factory=lambda: {"event": 1.0, "quoted": 0.6, "none": 0.3})
    non_primary: float = 0.5      # horizons other than the one the model calls its strongest

    def __post_init__(self) -> None:
        if not (0 < self.per_name <= self.gross <= 4) or set(self.base) != set(HOLD):
            raise ValueError("invalid autopilot caps")
        if not (0 < self.max_drawdown < 1 and 0 < self.day_stop < 1 and 0 < self.swing_stop < 1):
            raise ValueError("invalid autopilot stops")
        if any(not (0 <= w <= self.per_name) for w in self.base.values()) or not 0 <= self.against_trend <= 1:
            raise ValueError("invalid autopilot weights")
        if not (0 < self.risk_per_trade <= 0.05 and self.stop_atr > 0 and 0 <= self.non_primary <= 1
                and set(self.support) == {"event", "quoted", "none"} and all(0 <= v <= 1 for v in self.support.values())):
            raise ValueError("invalid autopilot sizing rules")

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Config:
        return cls(**{k: v for k, v in d.items() if not k.startswith("_")})


def regime(closes: list[float], window: int = 50) -> str:
    """'bull' when the last close is above its `window`-day average, else 'bear'; 'unknown' without enough data."""
    if len(closes) < window:
        return "unknown"
    return "bull" if closes[-1] > sum(closes[-window:]) / window else "bear"


def add_sessions(d: date, n: int) -> date:
    """The n-th NYSE session after d (n=0: d itself if it is a session, else the next one)."""
    while not is_session(d):
        d += timedelta(days=1)
    for _ in range(n):
        d += timedelta(days=1)
        while not is_session(d):
            d += timedelta(days=1)
    return d


def call(rating: int) -> tuple[int, float]:
    """(side, strength) of a 1-5 rating: +1 long / -1 short / 0 PASS; strength 2 for 5 and 1."""
    if rating >= 4:
        return 1, 2.0 if rating == 5 else 1.0
    if 1 <= rating <= 2:
        return -1, 2.0 if rating == 1 else 1.0
    return 0, 0.0


def size(h: str, strength: float, against: bool, support: str, primary: str | None, atr: float | None,
         cfg: Config) -> tuple[float, list[str]]:
    """The weight (fraction of equity) of one call, and the rules that set it, in order:
    1. base weight of the horizon; x2 for a strong call (rating 5 or 1);
    2. x support: a call backed by a company event keeps it all, a quote about the share price 0.6, no valid quote 0.3;
    3. x0.5 for a horizon that is not the model's primary one (when it named one);
    4. x0.5 against the market regime (shorts in a bull market, longs in a bear market);
    5. capped so a 1.5-ATR move over the holding period loses at most `risk_per_trade` of equity (volatile stocks
       and long holds get less), and at `per_name`."""
    w = cfg.base[h] * strength
    why = [f"base {cfg.base[h]:.0%}" + (" x2 strong" if strength > 1 else "")]
    f = cfg.support.get(support, cfg.support["none"])
    if f != 1.0:
        w *= f
        why.append(f"x{f:g} {support} support")
    if primary and primary in HOLD and h != primary:
        w *= cfg.non_primary
        why.append(f"x{cfg.non_primary:g} not the primary horizon")
    if against:
        w *= cfg.against_trend
        why.append(f"x{cfg.against_trend:g} against the market")
    if atr and atr > 0:
        cap = cfg.risk_per_trade / (cfg.stop_atr * atr * max(1, HOLD[h]) ** 0.5)
        if cap < w:
            w = cap
            why.append(f"risk cap {cap:.1%} (ATR {atr:.1%})")
    if w > cfg.per_name:
        w = cfg.per_name
        why.append(f"per-name cap {cfg.per_name:.0%}")
    return w, why


def new_lots(decision: dict[str, Any], session: date, mkt: str, cfg: Config,
             lots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lots opened by one research decision for `session`. decision: ticker, decided_at, ratings{horizon: 1-5};
    optional: support{horizon: event|quoted|none} (default event, for older records), primary horizon, atr (daily, a
    fraction of price), day_feasible (False: no day trade) and theme_horizons (a themed stock trades only on its
    themes' horizons: the user's themes of 4 Oct 2026, config/themes_book.json).
    A (symbol, horizon) lot already made from the same decision is not made twice; an open lot from an older
    decision on the same (symbol, horizon) is replaced by the new call (the caller closes it)."""
    made = {lot["key"] for lot in lots}
    out = []
    for h, rating in sorted((decision.get("ratings") or {}).items()):
        if h not in HOLD:
            continue
        side, strength = call(int(rating))
        key = f"{decision['ticker']}:{h}:{decision['decided_at']}"
        if not side or key in made or (h == "day" and decision.get("day_feasible") is False):
            continue
        if decision.get("theme_horizons") is not None and h not in decision["theme_horizons"]:
            continue
        against = (mkt == "bull" and side < 0) or (mkt == "bear" and side > 0)
        weight, why = size(h, strength, against, (decision.get("support") or {}).get(h, "event"),
                           decision.get("primary"), decision.get("atr"), cfg)
        if weight <= 0:
            continue
        out.append({"key": key, "symbol": decision["ticker"], "horizon": h, "side": side, "rating": int(rating),
                    "weight": round(weight, 6), "sizing": why, "decided_at": decision["decided_at"],
                    "theme": decision.get("theme"),
                    "regime": mkt, "session": session.isoformat(),
                    "exit_session": add_sessions(session, HOLD[h]).isoformat(), "state": "open",
                    "entry_price": None, "evidence": decision.get("evidence")})
    return out


def supersede(lots: list[dict[str, Any]], fresh: list[dict[str, Any]], reason: str = "replaced by a newer call") -> None:
    """Close open lots on the same (symbol, horizon) as a fresh lot."""
    pairs = {(x["symbol"], x["horizon"]) for x in fresh}
    for lot in lots:
        if lot["state"] == "open" and (lot["symbol"], lot["horizon"]) in pairs:
            lot.update(state="closed", closed_reason=reason)


def expire(lots: list[dict[str, Any]], session: date, flatten_day: bool) -> list[dict[str, Any]]:
    """Close lots whose holding period is over: swing lots on their exit session (sold at its open), day lots once
    `flatten_day` (the close is near) or when their session is past. Returns the lots closed now."""
    closed = []
    for lot in lots:
        if lot["state"] != "open":
            continue
        ends = date.fromisoformat(lot["exit_session"])
        if (lot["horizon"] == "day" and (flatten_day or ends < session)) or (lot["horizon"] != "day" and ends <= session):
            lot.update(state="closed", closed_reason="holding period over")
            closed.append(lot)
    return closed


def stop_out(lots: list[dict[str, Any]], prices: dict[str, float], cfg: Config) -> list[dict[str, Any]]:
    """Close open lots that moved past their stop from the entry price."""
    hit = []
    for lot in lots:
        p, e = prices.get(lot["symbol"]), lot.get("entry_price")
        if lot["state"] != "open" or not p or not e:
            continue
        stop = cfg.day_stop if lot["horizon"] == "day" else cfg.swing_stop
        if lot["side"] * (p / e - 1) <= -stop:
            lot.update(state="closed", closed_reason=f"stop {stop:.0%} hit at {p:.2f} (entry {e:.2f})")
            hit.append(lot)
    return hit


def weights(lots: list[dict[str, Any]], cfg: Config) -> dict[str, float]:
    """Net weight per symbol from the open lots, capped per name, then scaled so the gross fits."""
    w: dict[str, float] = {}
    for lot in lots:
        if lot["state"] == "open":
            w[lot["symbol"]] = w.get(lot["symbol"], 0.0) + lot["side"] * lot["weight"]
    w = {s: max(-cfg.per_name, min(cfg.per_name, x)) for s, x in w.items() if abs(x) > 1e-9}
    gross = sum(abs(x) for x in w.values())
    if gross > cfg.gross:
        w = {s: x * cfg.gross / gross for s, x in w.items()}
    return w


def target_shares(w: dict[str, float], equity: float, prices: dict[str, float]) -> dict[str, int]:
    """Whole shares toward zero; symbols without a price are left out (the caller keeps their position)."""
    return {s: math.trunc(x * equity / prices[s]) for s, x in w.items() if prices.get(s)}


def orders(targets: dict[str, int], held: dict[str, float], prices: dict[str, float],
           cfg: Config) -> list[dict[str, Any]]:
    """Orders moving `held` (positions plus open orders) to `targets`. Symbols in `held` but not in `targets` go
    to zero. A flip closes first. Reductions come first so buying power frees up before new risk is added."""
    out = []
    for s in sorted(set(targets) | set(held)):
        cur, tgt = held.get(s, 0.0), float(targets.get(s, 0))
        if cur * tgt < 0:
            tgt = 0.0
        delta = tgt - cur
        if abs(delta) < 1 or abs(delta) * prices.get(s, 0.0) < cfg.min_notional and tgt != 0:
            continue
        reducing = abs(tgt) < abs(cur)
        out.append({"symbol": s, "side": "buy" if delta > 0 else "sell", "qty": int(abs(delta)) if tgt else abs(cur),
                    "reducing": reducing, "from": cur, "to": tgt})
    return sorted(out, key=lambda o: not o["reducing"])


def drawdown_hit(equity: float, start: float, cfg: Config) -> bool:
    return start > 0 and equity <= start * (1 - cfg.max_drawdown)
