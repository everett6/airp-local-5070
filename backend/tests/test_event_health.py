"""Synthetic forward event input checks; no network or trading scripts."""
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from forward_events import validate_event_inputs

from app.sandbox.events import Prices


def sample(day="2026-09-28"):
    rows = [{"Date": day, "Ticker": t, "Open": 100.0, "Close": 100.0}
            for t in ("SPY", "AAA", "XLK")]
    prices = Prices.from_long(pd.DataFrame(rows))
    events = pd.DataFrame([{"accession": "a", "ticker": "AAA", "sector": "Information Technology",
                            "accepted_utc": "2026-09-29T20:00:00"}])
    return events, prices


def test_forward_check_requires_fresh_prices():
    events, prices = sample("2026-09-20")
    with pytest.raises(ValueError, match="SPY close stale"):
        validate_event_inputs(events, prices, datetime(2026, 9, 29, 22, tzinfo=UTC))


def test_forward_check_requires_cards_and_finite_scores(tmp_path):
    events, prices = sample()
    now = datetime(2026, 9, 29, 22, tzinfo=UTC)
    cards = tmp_path / "cards.csv"
    cards.write_text("accession,fact_sheet\n")
    with pytest.raises(ValueError, match="fact sheet absent"):
        validate_event_inputs(events, prices, now, feats=cards)
    cards.write_text("accession,fact_sheet\na,Valid synthetic card\n")
    validate_event_inputs(events, prices, now, feats=cards)
    with pytest.raises(ValueError, match="model score absent"):
        validate_event_inputs(events, prices, now, logodds={})
    validate_event_inputs(events, prices, now, logodds={"a": 3.1})
