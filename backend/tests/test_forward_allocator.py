"""Forward allocator: orders fill only at an open after the run's UTC date; today's (maybe partial) bar is ignored."""
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from app.portfolio.forward import new_books, step
from app.portfolio.master import MasterConfig


def frames(n: int = 200, end: str = "2026-09-25"):
    days = pd.bdate_range(end=end, periods=n)
    up = np.linspace(100, 200, n)
    opens = pd.DataFrame({"SPY": up, "BTC-USD": up * 500, "ETH-USD": up * 20}, index=days)
    return opens, opens * 1.001


def test_first_run_only_decides_and_ignores_todays_bar():
    opens, closes = frames()
    books = new_books()
    rec = step(books, opens, closes, datetime(2026, 9, 25, 15, 0, tzinfo=UTC), MasterConfig())
    assert rec["data_through"] == "2026-09-24"           # the 2026-09-25 bar is still forming
    assert all(b.positions == {} and b.pending for b in books.values())
    assert rec["books"]["master"]["new_targets"]["BTC-USD"] > 0  # uptrend: crypto sleeve on


def test_second_run_fills_at_the_first_open_after_the_decision_date():
    opens, closes = frames(end="2026-10-02")
    books = new_books()
    step(books, opens[opens.index <= "2026-09-25"], closes[closes.index <= "2026-09-25"],
         datetime(2026, 9, 25, 15, 0, tzinfo=UTC), MasterConfig())
    opens.loc["2026-09-25"] = 1e9  # the decision day's own open must never be used
    rec = step(books, opens, closes, datetime(2026, 10, 2, 15, 0, tzinfo=UTC), MasterConfig())
    assert rec["books"]["SPY"]["filled_on"] == "2026-09-28"
    fill = rec["books"]["SPY"]["fills"][0]
    assert fill["asset"] == "SPY" and fill["price"] == opens.loc["2026-09-28", "SPY"]
    assert books["SPY"].pending is None                    # bought once, then held
    assert books["master"].pending is not None             # re-decided each run
