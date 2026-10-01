import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

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
