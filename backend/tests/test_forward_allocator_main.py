"""The weekly rebalance's main body (scripts/forward_allocator.py) on synthetic prices in a scratch folder: what it
writes, that it runs once a day, and that bad prices stop it before anything is written. No network, no git."""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import forward_allocator as FA

from app.portfolio.forward import AGGRESSIVE, BOOKS
from app.sandbox.events import Prices


def _prices(last: datetime) -> Prices:
    """The same made-up history from a fixed start to `last`: a later call only adds days."""
    days = pd.bdate_range(start=(datetime.now(UTC) - timedelta(days=460)).date(), end=last.date())
    rows = []
    for k, (t, mu) in enumerate((("SPY", 0.0006), ("BTC-USD", 0.0015), ("ETH-USD", 0.001), ("SGOV", 0.0002))):
        path = 100 * np.cumprod(1 + np.random.default_rng(k).normal(mu, 0.004, len(days)))
        rows += [{"Date": d.date().isoformat(), "Ticker": t, "Open": float(v), "Close": float(v)}
                 for d, v in zip(days, path, strict=True)]
    return Prices.from_long(pd.DataFrame(rows))


@pytest.fixture
def allocator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    days = pd.bdate_range(end=(datetime.now(UTC) - timedelta(days=1)).date(), periods=2)  # the last two market days
    box = {"last": days[0].to_pydatetime(), "then": days[1].to_pydatetime(), "dir": tmp_path / "alloc"}

    def fetch(_now: datetime) -> Prices:
        FA.SOURCES.update(dict.fromkeys(FA.ASSETS, "test"))
        return _prices(box["last"])
    monkeypatch.setattr(FA, "fetch", fetch)
    monkeypatch.setattr(FA, "state", lambda: "ACTIVE")
    monkeypatch.setattr(sys, "argv", ["forward_allocator.py", "--dir", str(box["dir"]), "--no-commit"])
    monkeypatch.setattr(FA, "DIR", FA.DIR)  # main() rebinds the module's folder for --dir: put it back afterwards
    return box


def test_a_run_writes_one_ledger_line_and_the_books_and_refuses_a_second_run_that_day(allocator: dict) -> None:
    FA.main()
    d = allocator["dir"]
    lines = [json.loads(x) for x in (d / "ledger.jsonl").read_text().splitlines()]
    assert len(lines) == 1 and set(lines[0]["books"]) == set(BOOKS)
    rec = lines[0]
    assert rec["price_sources"] == dict.fromkeys(FA.ASSETS, "test") and rec["drawdown"]["book"] == "master+brakes"
    assert rec["drawdown_leveraged"][AGGRESSIVE]["limit_at"] == 0.6 and "aggressive_issues" not in rec
    state = json.loads((d / "state.json").read_text())
    assert set(state) == set(BOOKS) and not list(d.glob("*.tmp"))
    saved = pd.read_parquet(d / "factor_closes.parquet")
    assert set(saved.columns) >= {"SPY", "BTC-USD", "ETH-USD"} and all(saved.index.date < datetime.now(UTC).date())
    # the aggressive book wants 2.5 times the frozen book's targets, asset by asset
    frozen, agg = state["master+brakes"]["pending"], state[AGGRESSIVE]["pending"]
    assert set(agg) == set(frozen) and all(abs(agg[a] - 2.5 * frozen[a]) < 1e-3 for a in frozen)
    with pytest.raises(SystemExit, match="already ran today"):
        FA.main()
    assert len((d / "ledger.jsonl").read_text().splitlines()) == 1

    # the next run (the first one moved a week back): last week's targets fill at the first open after it
    week_ago = (datetime.now(UTC) - timedelta(days=7)).isoformat(timespec="seconds")
    rec["run_at_utc"] = week_ago
    (d / "ledger.jsonl").write_text(json.dumps(rec) + "\n" + '{"run_at_utc": "2026-')  # and a write cut off by a power loss
    (d / "state.json").write_text(json.dumps({k: v | {"decided_at": week_ago} for k, v in state.items()}))
    allocator["last"] = allocator["then"]  # one more market day since the first run
    FA.main()
    second = json.loads((d / "ledger.jsonl").read_text().splitlines()[2])  # on its own line, after the torn one
    assert all(np.isfinite(second["books"][b]["equity"]) and second["books"][b]["equity"] > 0 for b in BOOKS)
    assert 0 < second["books"][AGGRESSIVE]["gross"] <= 2.5 + 1e-6


def test_stale_prices_stop_the_run_before_anything_is_written(allocator: dict) -> None:
    allocator["last"] = datetime.now(UTC) - timedelta(days=12)
    with pytest.raises(ValueError, match="close stale"):
        FA.main()
    assert not (allocator["dir"] / "ledger.jsonl").exists() and not (allocator["dir"] / "state.json").exists()
