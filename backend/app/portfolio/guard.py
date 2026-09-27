"""Safety layer for the forward books, after Vibe-Trading's mandate / pre-trade gate / kill switch (HKUDS/Vibe-Trading).

  mandate      backend/config/mandate.json, committed to git: which assets may be held, the most any one asset may
               weigh, gross exposure, and the crypto share. Only the user changes it (a new commit).
  gate         every set of target weights is checked against the mandate when it is decided AND again just before
               it fills. It fails closed: a bad number, a missing or unreadable mandate, or any breach rejects the whole
               order set; the book keeps what it holds and the rejection is logged.
  kill switch  results/forward/HALT, with a mode (the trading states of NautilusTrader's risk engine):
                 HALTED    the allocator marks the books but fills and decides nothing;
                 REDUCING  orders may only shrink positions: every target weight is capped at the asset's current
                           weight, so nothing new is bought and nothing is added to.
               Anyone may create it (scripts, the viewer's Halt button); only `forward_allocator.py --resume` removes it.
The frozen book (SPY + 20% crypto cap, brakes) sits inside the default mandate, so the gate never binds on it.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[2]
MANDATE = BACKEND / "config" / "mandate.json"
HALT = BACKEND / "results" / "forward" / "HALT"
EPS = 1e-9


class MandateError(ValueError):
    pass


@dataclass(frozen=True)
class Mandate:
    universe: frozenset[str]
    crypto: frozenset[str]
    max_weight: float
    max_gross: float
    max_crypto: float


def _num(d: dict[str, Any], k: str, lo: float, hi: float) -> float:
    v = d.get(k)
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v) or not lo <= v <= hi:
        raise MandateError(f"mandate field {k!r} must be a number in [{lo}, {hi}], got {v!r}")
    return float(v)


def load_mandate(path: Path = MANDATE) -> Mandate:
    try:
        d = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise MandateError(f"mandate unreadable: {e}") from e
    uni, cry = d.get("universe"), d.get("crypto")
    if not isinstance(uni, list) or not uni or not all(isinstance(a, str) and a for a in uni):
        raise MandateError("mandate 'universe' must be a non-empty list of symbols")
    if not isinstance(cry, list) or not set(cry) <= set(uni):
        raise MandateError("mandate 'crypto' must be a list of symbols inside the universe")
    return Mandate(frozenset(uni), frozenset(cry), _num(d, "max_weight", 0, 1), _num(d, "max_gross", 0, 1),
                   _num(d, "max_crypto", 0, 1))


def check(targets: dict[str, Any], m: Mandate) -> list[str]:
    """Every breach of the mandate in these target weights (empty = allowed)."""
    bad: list[str] = []
    for a, w in targets.items():
        if isinstance(w, bool) or not isinstance(w, int | float) or not math.isfinite(w):
            bad.append(f"{a}: weight {w!r} is not a finite number")
        elif w < -EPS:
            bad.append(f"{a}: short weight {w:.4f} (shorting is not in the mandate)")
        elif w > m.max_weight + EPS:
            bad.append(f"{a}: weight {w:.4f} above the {m.max_weight:.2f} cap")
        if a not in m.universe:
            bad.append(f"{a}: not in the mandate's universe")
    if bad:
        return bad
    gross = sum(abs(w) for w in targets.values())
    crypto = sum(w for a, w in targets.items() if a in m.crypto)
    if gross > m.max_gross + EPS:
        bad.append(f"gross exposure {gross:.4f} above {m.max_gross:.2f}")
    if crypto > m.max_crypto + EPS:
        bad.append(f"crypto share {crypto:.4f} above {m.max_crypto:.2f}")
    return bad


def gate(targets: dict[str, Any], path: Path = MANDATE) -> list[str]:
    """check() against the mandate on disk; an unreadable mandate rejects everything (fail closed)."""
    try:
        return check(targets, load_mandate(path))
    except MandateError as e:
        return [str(e)]


def state(path: Path = HALT) -> str:
    """ACTIVE, HALTED or REDUCING. An unreadable HALT file counts as HALTED (fail closed)."""
    if not path.exists():
        return "ACTIVE"
    info = halt_info(path) or {}
    return "REDUCING" if info.get("mode") == "REDUCING" else "HALTED"


def halted(path: Path = HALT) -> bool:
    return state(path) == "HALTED"


def halt(reason: str, by: str, path: Path = HALT, mode: str = "HALTED") -> None:
    """Engage the kill switch. HALTED always wins: it may replace REDUCING, never the other way round; a second halt
    keeps the first record."""
    if mode not in ("HALTED", "REDUCING"):
        raise ValueError(f"unknown mode {mode!r}")
    if path.exists() and not (mode == "HALTED" and state(path) == "REDUCING"):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"at": datetime.now(UTC).isoformat(timespec="seconds"), "by": by,
                                "reason": reason, "mode": mode}) + "\n")


def reduce_only(targets: dict[str, float], positions: dict[str, float], cash: float, px: dict[str, float]
                ) -> dict[str, float]:
    """REDUCING: each target weight capped at the asset's current weight at these prices (new assets get 0)."""
    equity = cash + sum(q * px[a] for a, q in positions.items() if a in px)
    cur = {a: q * px[a] / equity for a, q in positions.items() if a in px} if equity > 0 else {}
    return {a: min(w, cur.get(a, 0.0)) for a, w in targets.items()}


def halt_info(path: Path = HALT) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return dict(json.loads(path.read_text()))
    except (OSError, json.JSONDecodeError):
        return {"reason": "HALT file present (unreadable)"}
