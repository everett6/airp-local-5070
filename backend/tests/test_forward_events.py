import sys
from datetime import UTC, date, datetime
from pathlib import Path

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
