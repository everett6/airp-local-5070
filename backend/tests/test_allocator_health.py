"""Synthetic checks for forward allocator inputs and outputs."""
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from forward_allocator import ASSETS, validate_prices, validate_result

from app.portfolio.forward import Book
from app.sandbox.events import Prices


def sample(day="2026-09-28"):
    rows = [{"Date": day, "Ticker": t, "Open": 100.0, "Close": 100.0} for t in ASSETS]
    return Prices.from_long(pd.DataFrame(rows))


def test_allocator_rejects_stale_or_missing_close():
    now = datetime(2026, 9, 29, 22, tzinfo=UTC)
    validate_prices(sample(), now)
    with pytest.raises(ValueError, match="SPY close stale"):
        validate_prices(sample("2026-09-20"), now)
    p = sample()
    p.close.loc[:, "ETH-USD"] = float("nan")
    with pytest.raises(ValueError, match="ETH-USD close missing"):
        validate_prices(p, now)


def test_allocator_rejects_bad_result_before_write():
    books = {"master": Book("master")}
    good = {"books": {"master": {"equity": 100000, "new_targets": {"SPY": 0.98}}},
            "price_sources": {"SPY": "yahoo"}}
    validate_result(good, books)
    bad = {"books": {"master": {"equity": 100000, "rejected": ["bad weights"]}},
           "price_sources": {"SPY": "yahoo"}}
    with pytest.raises(ValueError, match="target weights rejected"):
        validate_result(bad, books)
