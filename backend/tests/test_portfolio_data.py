import json
from datetime import date
from pathlib import Path

import pandas as pd

from app.dashboard import portfolio_data as P
from app.forward.ledger import Ledger

RUNS = [
    {"run_at_utc": "2026-10-05T21:00:00+00:00", "data_through": "2026-10-02",
     "books": {"master": {"equity": 100000.0, "positions": {}}, "SPY": {"equity": 100000.0, "positions": {}}}},
    {"run_at_utc": "2026-10-12T21:00:00+00:00", "data_through": "2026-10-09",
     "books": {"master": {"equity": 101000.0, "filled_on": "2026-10-06",
                          "fills": [{"asset": "SPY", "qty": 100, "price": 600.0},
                                    {"asset": "BTC-USD", "qty": 0.1, "price": 100000.0}]},
               "SPY": {"equity": 100500.0, "filled_on": "2026-10-06",
                       "fills": [{"asset": "SPY", "qty": 160, "price": 600.0}]}}},
]


def test_equity_history_and_trades():
    h = P.equity_history(RUNS)
    assert list(h.columns) == ["master", "SPY"] and h.loc["2026-10-09", "master"] == 101000.0
    t = P.trades(RUNS)
    assert len(t) == 3 and set(t["side"]) == {"buy"}
    assert t[t["asset"] == "BTC-USD"]["value"].iloc[0] == 10000.0


def test_mark_values_and_refuses_partial():
    b = {"cash": 10000.0, "positions": {"SPY": 100, "BTC-USD": 0.1}, "peak": 100000.0}
    m = P.mark(b, {"SPY": 690.0, "BTC-USD": 100000.0})
    assert m["equity"] == 89000.0 and abs(m["drawdown"] - 0.11) < 1e-9 and abs(m["brake"] - 2 / 3) < 1e-9
    assert abs(sum(p["weight"] for p in m["positions"]) - 79000 / 89000) < 1e-9
    m2 = P.mark(b, {"SPY": 700.0})
    assert m2["equity"] is None and m2["unpriced"] == ["BTC-USD"]


def _ledger(tmp_path: Path) -> Path:
    led = Ledger(tmp_path / "events" / "ledger.jsonl")
    led.path.parent.mkdir(parents=True)
    led.append("run", as_of="2026-10-05T12:45:00+00:00", new=0)
    led.append("decision", accession="a1", ticker="AZO", sector="Consumer Discretionary",
               entry_deadline="2026-10-06T09:30:00-04:00", source="bonsai", logodds=0.5, on_time=True)
    led.append("decision", accession="a2", ticker="CTAS", sector="Industrials",
               entry_deadline="2026-10-07T09:30:00-04:00", source="bonsai", logodds=-0.2, on_time=True)
    led.append("missed", accession="a3", ticker="X", entry_deadline="2026-10-07T09:30:00-04:00",
               reason="decided after the entry open: never backfilled")
    led.append("outcome", accession="a1", entry="2026-10-06", fwd5=0.02)
    led.append("run", as_of="2026-10-08T22:00:00+00:00", new=2)
    return tmp_path


def test_picks_scoreboard_and_health(tmp_path):
    fwd = _ledger(tmp_path)
    recs, err = P.load_events("events", fwd)
    assert err is None
    p = P.picks(recs)
    assert dict(zip(p["accession"], p["status"], strict=True)) == {"a1": "closed", "a2": "open", "a3": "missed"}
    assert p.set_index("accession").at["a2", "entry"] == "2026-10-07"
    s = P.scoreboard(p)
    assert (s["decisions"], s["open"], s["closed"], s["missed"], s["bonsai_ic"]) == (2, 1, 1, 1, None)
    assert P.missed_weekdays(recs, today=date(2026, 10, 10)) == [date(2026, 10, 6), date(2026, 10, 7), date(2026, 10, 9)]
    assert P.event_dirs(fwd) == ["events"]


def test_broken_ledger_is_reported(tmp_path):
    fwd = _ledger(tmp_path)
    f = fwd / "events" / "ledger.jsonl"
    lines = f.read_text().splitlines()
    r = json.loads(lines[1])
    r["logodds"] = 9.9
    f.write_text("\n".join([lines[0], json.dumps(r), *lines[2:]]) + "\n")
    recs, err = P.load_events("events", fwd)
    assert err and "line 2" in err and len(recs) == 6


def test_live_excess_open_picks_only():
    p = pd.DataFrame([{"accession": "a2", "ticker": "CTAS", "sector": "Industrials", "entry": "2026-10-07",
                       "status": "open"},
                      {"accession": "a4", "ticker": "CTAS", "sector": "Industrials", "entry": "2026-10-09",
                       "status": "open"}])
    idx = pd.DatetimeIndex(["2026-10-07", "2026-10-08"])
    opens = pd.DataFrame({"CTAS": [100.0, 101.0], "XLI": [50.0, 50.0]}, index=idx)
    closes = pd.DataFrame({"CTAS": [102.0, 105.0], "XLI": [50.5, 51.0]}, index=idx)
    x = P.live_excess(p, opens, closes)
    assert list(x.index) == ["a2"] and abs(x["a2"] - (0.05 - 0.02)) < 1e-12


def test_run_log_newest_first_with_every_job(tmp_path):
    fwd = _ledger(tmp_path)
    recs, _ = P.load_events("events", fwd)
    runs = [RUNS[0], {**RUNS[1], "books": {"master": {**RUNS[1]["books"]["master"],
                                                         "rejected": ["crypto share 0.3000 above 0.20"]}}}]
    log = P.run_log(runs, recs, {"at": "2026-10-13T00:00:00+00:00", "by": "viewer", "reason": "test"})
    assert log[0]["job"] == "kill switch" and log[1]["job"] == "allocator"
    assert "filled +100 SPY @ 600.00" in log[1]["what"] and "REJECTED by mandate" in log[1]["what"]
    whats = " | ".join(x["what"] for x in log)
    assert "MISSED" in whats and "+2.00% vs sector" in whats and "AZO score +0.50" in whats
    assert [x["at"] for x in log] == sorted((x["at"] for x in log), reverse=True)


def test_underwater():
    u = P.underwater(pd.Series([100.0, 110.0, 99.0, 121.0]))
    assert list(u.round(4)) == [0.0, 0.0, -0.1, 0.0]
