import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import forward_events as fe
from forward_events import entry_deadline, score

from app.forward.schedule import NY


def test_after_close_release_enters_at_next_open():
    # 16:05 ET Tuesday (20:05 UTC in summer) -> Wednesday 09:30 ET
    assert entry_deadline("2026-09-22T20:05:00") == datetime(2026, 9, 23, 9, 30, tzinfo=NY)


def test_pre_open_release_enters_the_same_morning():
    # 07:00 ET Tuesday -> Tuesday 09:30 ET
    assert entry_deadline("2026-09-22T11:00:00") == datetime(2026, 9, 22, 9, 30, tzinfo=NY)


def test_friday_evening_and_weekend_releases_enter_on_monday():
    assert entry_deadline("2026-09-25T21:00:00") == datetime(2026, 9, 28, 9, 30, tzinfo=NY)
    assert entry_deadline("2026-09-26T15:00:00") == datetime(2026, 9, 28, 9, 30, tzinfo=NY)


def test_score_counts_each_source_separately_and_ignores_missed():
    recs = [{"type": "decision", "accession": f"a{i}", "source": "bonsai", "logodds": i, "on_time": True}
            for i in range(12)]
    recs += [{"type": "outcome", "accession": f"a{i}", "fwd5": i / 100} for i in range(12)]
    recs += [{"type": "missed", "accession": "m1"}, {"type": "decision", "accession": "l1", "source": "lite",
                                                      "logodds": 0.1, "on_time": True}]
    s = score(recs)
    assert s["bonsai_ic"] == 1.0 and s["bonsai_n"] == 12
    assert "lite_ic" not in s  # fewer than 10 scored lite decisions
    assert s["missed"] == 1 and s["on_time"] == 13


def test_lite_features_use_only_bars_before_the_entry():
    import numpy as np
    import pandas as pd
    import pytest
    from forward_events import pre_entry

    from app.sandbox.events import Prices
    idx = pd.bdate_range("2025-01-01", "2026-09-22")
    rows = []
    for t, drift in (("AAA", 0.001), ("XLK", 0.0), ("SPY", 0.0)):
        c = 100 * np.exp(drift * np.arange(len(idx)))
        rows += [{"Date": d.date().isoformat(), "Ticker": t, "Open": v, "High": v, "Low": v, "Close": v, "Volume": 1}
                 for d, v in zip(idx, c, strict=True)]
    p = Prices.from_long(pd.DataFrame(rows))
    ev = pd.DataFrame([{"accession": "a", "ticker": "AAA", "sector": "Information Technology",
                        "accepted_utc": "2026-09-22T20:05:00"},  # after the close: entry is the 23rd, not in the data
                       {"accession": "b", "ticker": "AAA", "sector": "Information Technology",
                        "accepted_utc": "2026-09-22T11:00:00"}])  # pre-open: entry is the 22nd
    out = pre_entry(ev, p).set_index("accession")
    assert out.loc["a", "momentum"] > 0 and out.loc["b", "momentum"] > 0

    assert out.loc["a", "momentum"] == pytest.approx(np.exp(0.001 * 231) - 1)
    assert out.loc["b", "momentum"] == pytest.approx(np.exp(0.001 * 231) - 1)


def test_gpu_busy_ignores_desktop_apps():
    from forward_events import gpu_busy
    assert not gpu_busy("")
    assert not gpu_busy("/usr/bin/nautilus, 61\n/usr/lib/firefox/firefox, 150\n")
    assert gpu_busy("/usr/bin/nautilus, 61\n/home/u/.venv/bin/python, 900\n")
    assert gpu_busy("/usr/local/bin/ollama, 20000\n") and gpu_busy("some-game, 4096\n")
    assert gpu_busy("weird line without memory\n")


def test_failed_child_stops_event_pipeline(monkeypatch):
    import subprocess

    import forward_events as fe

    def failed(cmd, **kw):
        assert kw["check"] is True
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(fe.subprocess, "run", failed)
    with pytest.raises(subprocess.CalledProcessError):
        fe.run(["python", "scripts/build_features.py"])


def test_live_extract_has_no_backtest_end_date(tmp_path, monkeypatch):
    """29 Sep 2026: the extract step kept extract_events.py's backtest default --to 2026-09-24, so every new
    release was dropped and missed. Both GPU steps must read to the far future."""
    import forward_events as fe
    cmds = []
    monkeypatch.setattr(fe, "run", lambda cmd: cmds.append(cmd) or True)

    class NoServer:
        def __init__(self, *a):
            pass

        def stop(self):
            pass
    monkeypatch.setattr(fe, "Ollama", NoServer)
    monkeypatch.setattr(fe, "BACKEND", tmp_path)  # the extract path is written relative to BACKEND
    fe.fact_sheets(tmp_path, tmp_path / "ev.csv", date(2026, 9, 28), True, "t", tmp_path / "p")
    ext = next(c for c in cmds if "extract" in c)
    assert ext[ext.index("--to") + 1] == "2099-12-31"


def test_an_unreadable_older_page_of_a_filing_list_marks_the_company_unread():
    """Rehearsal replay, 1 Oct 2026: two identical lookups of 30 Apr found 38 and 34 releases and neither reported a
    problem. A busy filer's list is paged; a page that could not be fetched was skipped as if it held nothing."""
    import asyncio

    import build_events

    recent = {"form": ["8-K"], "items": ["2.02"], "filingDate": ["2026-09-30"], "accessionNumber": ["new"],
              "acceptanceDateTime": ["2026-09-30T20:10:00.000Z"], "primaryDocument": ["a.htm"]}
    old = {"form": ["8-K"], "items": ["2.02,9.01"], "filingDate": ["2026-04-30"], "accessionNumber": ["old"],
           "acceptanceDateTime": ["2026-04-30T11:00:00.000Z"], "primaryDocument": ["b.htm"]}

    class Resp:
        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    class FakeSec:
        def __init__(self, page_ok: bool):
            self.failed: set[int] = set()
            self.page_ok = page_ok

        async def get(self, url: str):
            if url.endswith("CIK0000000007.json"):
                return Resp({"filings": {"recent": recent, "files": [
                    {"name": "CIK0000000007-submissions-001.json", "filingFrom": "2020-01-01", "filingTo": "2026-06-30"}]}})
            return Resp(old) if self.page_ok else None

    ok = FakeSec(True)
    got = asyncio.run(build_events.company_events(ok, 7, "2026-04-29", "2026-04-30"))
    assert [e["accession"] for e in got] == ["old"] and ok.failed == set()
    bad = FakeSec(False)
    assert asyncio.run(build_events.company_events(bad, 7, "2026-04-29", "2026-04-30")) == []
    assert bad.failed == {7}  # not "no release": asked again, and named if still unread
    live = FakeSec(False)     # a live run's window is inside the recent block: no older page is asked for
    got = asyncio.run(build_events.company_events(live, 7, "2026-09-29", "2026-10-01"))
    assert [e["accession"] for e in got] == ["new"] and live.failed == set()


INDEX_PAGE = """<div class="formGrouping"><div class="infoHead">Filing Date</div><div class="info">2026-10-01</div>
<div class="infoHead">Accepted</div>
<div class="info">{when}</div></div>
<table><tr><td>1</td><td>8-K</td><td><a href="/Archives/edgar/data/1/a/main.htm">main.htm</a></td><td>8-K</td></tr>
<tr><td>2</td><td>PRESS RELEASE</td><td><a href="/Archives/edgar/data/1/a/ex991.htm">ex991.htm</a></td><td>EX-99.1</td></tr>
</table>"""


def test_the_acceptance_time_is_the_index_pages_new_york_clock_time():
    """1 Oct 2026: the filing list's time field was the New York clock time labelled UTC for every live release
    (4 hours early) and 4 or 5 hours late for older filings. The index page's "Accepted" is the one to trust."""
    import build_events

    assert build_events.index_accepted_utc(INDEX_PAGE.format(when="2026-10-01 16:15:15")) == "2026-10-01T20:15:15"
    assert build_events.index_accepted_utc(INDEX_PAGE.format(when="2026-01-16 06:37:11")) == "2026-01-16T11:37:11"  # EST
    assert build_events.index_accepted_utc("<html>no such field</html>") is None
    assert build_events.index_ex99(INDEX_PAGE.format(when="x")) == "https://www.sec.gov/Archives/edgar/data/1/a/ex991.htm"
    assert build_events.index_ex99("<table></table>") == ""


def test_the_filing_lists_time_no_longer_decides_the_entry_day():
    now = datetime(2026, 10, 2, 12, 45, tzinfo=UTC)  # Fri 08:45 New York
    found = [
        {"accession": "early", "accepted_utc": "2026-10-01T16:15:15"},  # the list: New York time labelled UTC
        {"accession": "late", "accepted_utc": "2026-10-02T14:53:18"},   # the list: 4 hours late, "after now"
        {"accession": "midday", "accepted_utc": "2026-10-01T10:30:00"},  # the list says 06:30 New York...
        {"accession": "future", "accepted_utc": "2026-10-02T12:00:00"},  # truly accepted after this run began
        {"accession": "nopage", "accepted_utc": "2026-10-01T21:00:00"},
    ]
    pages = [("u1", "2026-10-01T20:15:15"), ("u2", "2026-10-02T10:53:18"), ("u3", "2026-10-01T14:30:00"),
             ("u4", "2026-10-02T16:00:00"), ("", None)]
    kept, urls, stats = fe.true_times(found, pages, now)
    got = {e["accession"]: e["accepted_utc"] for e in kept}
    assert got == {"early": "2026-10-01T20:15:15", "late": "2026-10-02T10:53:18", "midday": "2026-10-01T14:30:00",
                   "nopage": "2026-10-01T21:00:00"} and urls == ["u1", "u2", "u3", ""]
    assert stats == {"times_checked": 4, "times_differed": 4, "times_from_list": ["nopage"]}
    assert found[0]["accepted_utc"] == "2026-10-01T16:15:15"  # the caller's records are not changed in place
    # what hangs on it: a pre-market release read 4 hours late would be entered a day late as if on time...
    assert fe.entry_deadline(got["late"]).date().isoformat() == "2026-10-02"
    assert fe.entry_deadline("2026-10-02T14:53:18").date().isoformat() == "2026-10-05"
    # ...and a release filed at 10:30 New York, read 4 hours early, would be called late for an open it never had
    assert fe.entry_deadline(got["midday"]).date().isoformat() == "2026-10-02"
    assert fe.entry_deadline("2026-10-01T10:30:00").date().isoformat() == "2026-10-01"


def test_one_entry_rule_where_the_two_old_rules_disagreed():
    """Since 1 Oct 2026 the deadline is the open of the scored entry session (accepted before 13:00 UTC on a trading
    day: that day; else the next). The old deadline rule (before 09:30 New York: that day) differed in this window."""
    from app.forward.schedule import entry_session
    from app.sandbox.events import entry_index
    # summer, 09:10 New York (13:10 UTC): scored from the next day, so decided for the next day (was: missed)
    assert entry_deadline("2026-09-22T13:10:00") == datetime(2026, 9, 23, 9, 30, tzinfo=NY)
    assert entry_deadline("2026-09-22T12:59:00") == datetime(2026, 9, 22, 9, 30, tzinfo=NY)
    # winter, 08:10 New York (13:10 UTC): the next day too (was: the same morning, a day before the scored trade)
    assert entry_deadline("2026-11-03T13:10:00") == datetime(2026, 11, 4, 9, 30, tzinfo=NY)
    assert entry_deadline("2026-11-03T12:50:00") == datetime(2026, 11, 3, 9, 30, tzinfo=NY)  # 07:50 New York
    # and it is the scoring rule's day for every hour of a fortnight
    days = pd.bdate_range("2026-10-26", "2026-11-20")
    for t in pd.date_range("2026-10-26", "2026-11-12 23:00", freq="h"):
        i = entry_index(days, t.to_pydatetime())
        assert days[i].date() == entry_session(t.to_pydatetime()) == entry_deadline(t.isoformat()).date()


def test_no_decision_is_due_on_a_market_holiday():
    from app.forward.schedule import is_session, nyse_holidays
    # Wed 25 Nov 2026 after the close -> not Thanksgiving (Thu 26) but Fri 27
    assert entry_deadline("2026-11-25T21:05:00") == datetime(2026, 11, 27, 9, 30, tzinfo=NY)
    assert entry_deadline("2026-11-26T12:00:00") == datetime(2026, 11, 27, 9, 30, tzinfo=NY)  # filed on the holiday
    assert entry_deadline("2026-04-02T20:30:00") == datetime(2026, 4, 6, 9, 30, tzinfo=NY)    # Good Friday, weekend
    assert sorted(nyse_holidays(2026)) == [date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
                                           date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
                                           date(2026, 11, 26), date(2026, 12, 25)]
    assert date(2027, 6, 18) in nyse_holidays(2027) and date(2027, 7, 5) in nyse_holidays(2027)  # Sat / Sun rules
    assert date(2027, 12, 24) in nyse_holidays(2027)
    assert is_session(date(2027, 12, 31)) and date(2028, 1, 1).weekday() == 5  # New Year on a Saturday: no Friday off
    assert date(2021, 6, 18) not in nyse_holidays(2021) and date(2022, 6, 20) in nyse_holidays(2022)  # Juneteenth


def test_an_outcome_enters_at_the_recorded_session_or_the_next_trading_day():
    from forward_events import outcome_index
    days = pd.DatetimeIndex(["2025-01-07", "2025-01-08", "2025-01-10", "2025-01-13"])  # 9 Jan 2025: closed (mourning)
    assert outcome_index(days, {"entry_session": "2025-01-08", "accepted_utc": "2025-01-07T21:00:00"}) == 1
    assert outcome_index(days, {"entry_session": "2025-01-09", "accepted_utc": "2025-01-08T21:00:00"}) == 2
    assert outcome_index(days, {"entry_session": "2025-01-14", "accepted_utc": "2025-01-13T21:00:00"}) is None
    assert outcome_index(days, {"accepted_utc": "2025-01-07T21:00:00"}) == 1  # an older record: the scoring rule
