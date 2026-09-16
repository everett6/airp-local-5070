"""
Point-in-time OHLCV bars and the Kronos baseline's inputs/outputs (stdlib only, so it is testable without torch).

Kronos (Shi et al., AAAI 2026, arXiv:2508.02739, github.com/shiyu-coder/Kronos, MIT) is a candlestick
foundation model whose pre-training data ends June 2024, before every test window in this repo. The heavy
part runs in `scripts/kronos_forecasts.py` (separate venv with torch); this module holds the parts that
decide what the model may see and how its samples become a probability, so they are covered by the main tests.
"""
from __future__ import annotations

import bisect
import csv
import hashlib
import json
import math
from datetime import date
from pathlib import Path
from typing import Any

Bar = tuple[date, float, float, float, float, float]  # date, open, high, low, close, volume

# fixed before the first v7 run; recorded in every forecast file and checked when loaded
KRONOS_PARAMS: dict[str, Any] = {
    "model": "NeoQuasar/Kronos-small", "model_revision": "901c26c1332695a2a8f243eb2f37243a37bea320",
    "tokenizer": "NeoQuasar/Kronos-Tokenizer-base", "tokenizer_revision": "0e0117387f39004a9016484a186a908917e22426",
    "code_commit": "67b630e67f6a18c9e9be918d9b4337c960db1e9a",
    "context_bars": 256, "samples": 24, "temperature": 1.0, "top_p": 0.9, "seed": 20260916,
}


def cutoffs_for(cfg: dict[str, Any], dates: list[date]) -> list[date]:
    """The walk-forward cutoffs of a config (same rule as walkforward.run), from the close-price file's dates."""
    def on_or_before(d: date) -> int:
        i = bisect.bisect_right(dates, d) - 1
        if i < 0:
            raise ValueError(f"no data on or before {d}")
        return i

    i0 = on_or_before(date.fromisoformat(str(cfg["start"])))
    last = len(dates) - 1 - int(cfg["horizon"])
    if cfg.get("end"):
        last = min(last, on_or_before(date.fromisoformat(str(cfg["end"]))))
    return [dates[i] for i in range(i0, last + 1, int(cfg["step"]))]


def load_ohlcv(path: Path) -> dict[str, list[Bar]]:
    out: dict[str, list[Bar]] = {}
    with path.open() as f:
        for row in csv.DictReader(f):
            bar = (date.fromisoformat(row["Date"][:10]), float(row["Open"]), float(row["High"]), float(row["Low"]),
                   float(row["Close"]), float(row["Volume"]))
            out.setdefault(row["Ticker"], []).append(bar)
    for bars in out.values():
        bars.sort(key=lambda b: b[0])
    return out


def window(bars: list[Bar], cutoff: date, n: int) -> list[Bar]:
    """The last `n` bars dated on or before `cutoff` (copies; later bars are never included)."""
    end = bisect.bisect_right([b[0] for b in bars], cutoff)
    out = bars[max(0, end - n):end]
    if out and out[-1][0] > cutoff:  # defence in depth
        raise AssertionError("window returned a bar after the cutoff")
    return list(out)


def sample_probability(sample_returns: list[float]) -> float:
    """Pre-registered mapping from Kronos's sampled h-day returns to P(up): the normal-approximation probability
    that the return is positive, Phi(mean/sd), shrunk halfway to 0.5 (a 24-sample estimate is noisy)."""
    n = len(sample_returns)
    if n < 2:
        return 0.5
    mean = sum(sample_returns) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in sample_returns) / (n - 1))
    if sd == 0:
        return 0.5 if mean == 0 else (0.75 if mean > 0 else 0.25)
    phi = 0.5 * (1 + math.erf(mean / sd / math.sqrt(2)))
    return 0.5 + 0.5 * (phi - 0.5)


def load_forecasts(path: Path, data_sha256: str, cutoffs: list[date], horizon: int) -> dict[tuple[date, str], float]:
    """Load a forecast file, refusing it unless it was made from the same OHLCV file, with the pre-registered
    parameters, for exactly these cutoffs and horizon."""
    meta = json.loads(path.with_suffix(".meta.json").read_text())
    problems = []
    if meta.get("ohlcv_sha256") != data_sha256:
        problems.append("OHLCV data differs")
    if meta.get("params") != KRONOS_PARAMS:
        problems.append("parameters differ from KRONOS_PARAMS")
    if meta.get("cutoffs") != [c.isoformat() for c in cutoffs] or meta.get("horizon") != horizon:
        problems.append("cutoffs or horizon differ from this run")
    if meta.get("forecasts_sha256") != hashlib.sha256(path.read_bytes()).hexdigest():
        problems.append("forecast file was modified after it was written")
    if problems:
        raise SystemExit(f"refusing Kronos forecasts {path.name}: " + "; ".join(problems))
    out: dict[tuple[date, str], float] = {}
    for line in path.read_text().splitlines():
        rec = json.loads(line)
        out[(date.fromisoformat(rec["cutoff"]), rec["ticker"])] = float(rec["p"])
    return out
