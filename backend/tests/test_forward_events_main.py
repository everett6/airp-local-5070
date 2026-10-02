"""The live decision runner's main body (scripts/forward_events.py) with stubbed discovery, prices, reader and judge:
what reaches the ledger. No GPU, no network."""
from __future__ import annotations

import contextlib
import gzip
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import extract_events
import forward_events as FE

from app.forward.ledger import Ledger
from app.sandbox.events import Prices

IT = "Information Technology"
URL = "https://www.sec.gov/x.htm"
RELEASES = [  # accepted (UTC) on Thu 1 Oct 2026: after the close (20:xx) or before the open (11:xx)
    ("a", "AAA", "2026-10-01T20:10:00", URL),   # normal
    ("b", "BBB", "2026-10-01T20:15:00", ""),    # the filing has no press release
    ("c", "CCC", "2026-10-01T20:20:00", URL),   # no price for the stock yet
    ("d", "DDD", "2026-10-01T11:00:00", URL),   # seen only after its open
    ("e", "EEE", "2026-10-01T11:05:00", None),  # no press release, and its open has passed
]


def _prices(tickers: list[str]) -> Prices:
    days = pd.bdate_range(end="2026-09-30", periods=300)
    rng = np.random.default_rng(0)
    rows = [{"Date": d.date().isoformat(), "Ticker": t, "Open": float(v), "Close": float(v)}
            for t in tickers for d, v in zip(days, 100 * np.cumprod(1 + rng.normal(0, 0.01, len(days))), strict=True)]
    return Prices.from_long(pd.DataFrame(rows))


@pytest.fixture
def runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"priced": ["SPY", *FE.SECTOR_ETF.values(), "AAA", "BBB", "DDD", "EEE"], "judged": []}

    async def discover(_since: Any, _now: Any, _indexes: Any = ()) -> pd.DataFrame:
        return pd.DataFrame([{"cik": i, "ticker": t, "index": "sp500", "sector": IT, "accession": a,
                              "accepted_utc": acc, "filed": acc[:10], "items": "2.02", "ex99_url": u}
                             for i, (a, t, acc, u) in enumerate(RELEASES)])

    def fact_sheets(d: Path, ev_csv: Path, _since: Any, _gpu: bool, _tag: str, _px: Path) -> Path:
        ev = pd.read_csv(ev_csv)
        rows = []
        for r in ev.itertuples():
            if isinstance(r.ex99_url, str) and r.ex99_url:  # the download step
                extract_events.text_path(r.accession).write_bytes(gzip.compress(b"release"))
                if r.ticker in state["priced"]:             # the fact-sheet step leaves out a stock without prices
                    rows.append({"accession": r.accession, "fact_sheet": f"sheet {r.ticker}", "guidance": "raised"})
        out = d / "features.csv"
        pd.DataFrame(rows, columns=["accession", "fact_sheet", "guidance"]).to_csv(out, index=False)
        return out

    def bonsai(_ev: Path, _ex: Path, feats: Path, *_a: Any) -> dict[str, float]:
        accs = list(pd.read_csv(feats)["accession"])
        state["judged"].append(accs)
        return dict.fromkeys(accs, 3.5)

    monkeypatch.setattr(extract_events, "TEXT", tmp_path)
    monkeypatch.setattr(FE, "discover", discover)
    monkeypatch.setattr(FE, "fact_sheets", fact_sheets)
    monkeypatch.setattr(FE, "bonsai", bonsai)
    monkeypatch.setattr(FE, "wait_gpu_free", lambda *_a, **_k: True)
    monkeypatch.setattr(FE, "gpu_priority", lambda _n: contextlib.nullcontext())
    monkeypatch.setattr(FE, "prices_for", lambda *_a, **_k: _prices(state["priced"]))

    def run(as_of: str) -> list[dict[str, Any]]:
        monkeypatch.setattr(sys, "argv", ["forward_events.py", "--dir", str(tmp_path / "ev"), "--as-of", as_of])
        FE.main()
        return Ledger(tmp_path / "ev" / "ledger.jsonl").verify()
    state["run"] = run
    return state


def test_one_undecidable_release_does_not_stop_the_others(runner: dict[str, Any],
                                                          capsys: pytest.CaptureFixture[str]) -> None:
    """About 1 in 70 earnings filings has no press release. Since the health checks of 29 Sep one such filing (or a
    stock without a price) made the whole run fail, and every later run with it: no decision for anyone. The
    registered rule is that an impossible decision is logged as missed while the others are decided."""
    recs = runner["run"]("2026-10-01T22:30:00")  # Thu 18:30 New York
    got = {r["accession"]: r for r in recs if r["type"] in ("decision", "missed")}
    assert set(got) == {"a", "d", "e"}
    assert got["a"]["type"] == "decision" and got["a"]["on_time"] and got["a"]["logodds"] == 3.5
    assert got["a"]["guidance"] == "raised" and got["a"]["source"] == "bonsai"
    assert (got["d"]["type"], got["d"]["reason"]) == ("missed", "decided after the entry open: never backfilled")
    assert (got["e"]["type"], got["e"]["reason"]) == ("missed", FE.NO_RELEASE)
    out = capsys.readouterr().out
    assert "not decidable yet, tried again next run: BBB b" in out and "CCC c: no decision (no price for CCC)" in out
    assert runner["judged"] == [["a", "d"]] and recs[-1]["type"] == "run"

    runner["priced"].append("CCC")  # its price arrives before its open: decided on time by the morning run
    recs = runner["run"]("2026-10-02T12:45:00")  # Fri 08:45 New York
    got = {r["accession"]: r for r in recs if r["type"] in ("decision", "missed")}
    assert set(got) == {"a", "c", "d", "e"} and got["c"]["type"] == "decision" and got["c"]["on_time"]
    assert runner["judged"][-1] == ["c"]  # nothing is judged twice

    recs = runner["run"]("2026-10-02T22:30:00")  # after its open: the release without a press release is closed out
    got = {r["accession"]: r for r in recs if r["type"] in ("decision", "missed")}
    assert (got["b"]["type"], got["b"]["reason"]) == ("missed", FE.NO_RELEASE)
    assert len(got) == 5 and len([r for r in recs if r["type"] == "run"]) == 3
    assert len(runner["judged"]) == 2  # nothing decidable was new in the third run: the judge was not started


def test_a_release_whose_fact_sheet_is_missing_still_fails_the_run(runner: dict[str, Any],
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """The health check itself stays: a release with a press release and prices but no fact sheet means the pipeline
    broke, and that run must fail loudly instead of recording misses."""
    def broken(d: Path, ev_csv: Path, *_a: Any) -> Path:
        for r in pd.read_csv(ev_csv).itertuples():
            if isinstance(r.ex99_url, str) and r.ex99_url:
                extract_events.text_path(r.accession).write_bytes(gzip.compress(b"release"))
        out = d / "features.csv"
        out.write_text("accession,fact_sheet,guidance\n")
        return out
    monkeypatch.setattr(FE, "fact_sheets", broken)
    with pytest.raises(ValueError, match="fact sheet absent"):
        runner["run"]("2026-10-01T22:30:00")


def test_prices_come_from_one_call_with_the_single_requests_as_fallback(monkeypatch: pytest.MonkeyPatch,
                                                                           capsys: pytest.CaptureFixture[str]) -> None:
    import yfinance

    days = pd.bdate_range("2026-09-01", periods=5, name="Date")
    calls: list[Any] = []

    def one(t: str) -> pd.DataFrame:
        base = {"AAA": 10.0, "BBB": 20.0, "CCC": 30.0}[t]
        return pd.DataFrame({c: base + np.arange(5.0) for c in FE.BAR_COLS[:4]} | {"Volume": np.arange(5) + 100},
                            index=days)

    def download(tickers: Any, **kw: Any) -> pd.DataFrame:
        calls.append(tickers)
        if isinstance(tickers, str):
            return pd.DataFrame() if tickers == "NONE" else one(tickers)
        if state["fail"]:
            raise RuntimeError("rate limited")
        frames = {t: one(t).astype({"Volume": "float64"}) for t in tickers if t in ("AAA", "BBB")}  # CCC left out
        return pd.concat(frames, axis=1, names=["Ticker", "Price"])

    state = {"fail": False}
    monkeypatch.setattr(yfinance, "download", download)
    got = FE.daily_bars(["AAA", "BBB", "CCC", "NONE"], "2026-09-01", "2026-09-08")
    assert calls == [["AAA", "BBB", "CCC", "NONE"], "CCC", "NONE"] and sorted(got) == ["AAA", "BBB", "CCC"]
    for t in got:  # the batched and the single results have the same shape, names and number types
        pd.testing.assert_frame_equal(got[t], one(t), check_exact=True)
    calls.clear()
    state["fail"] = True
    got = FE.daily_bars(["AAA", "BBB"], "2026-09-01", "2026-09-08")
    assert calls == [["AAA", "BBB"], "AAA", "BBB"] and sorted(got) == ["AAA", "BBB"]
    assert "the batched call failed (RuntimeError)" in capsys.readouterr().out


def test_a_company_lookup_that_fails_is_retried_and_reported_not_taken_for_no_release(monkeypatch: pytest.MonkeyPatch
                                                                                     ) -> None:
    """A failed SEC request used to look exactly like "no earnings filing" (review of 1 Oct 2026)."""
    import asyncio
    from datetime import UTC, date, datetime

    import build_events

    calls: dict[int, int] = {}

    class Sec:
        def __init__(self, _ua: str) -> None:
            self.failed: set[int] = set()
            self.client = type("C", (), {"aclose": staticmethod(lambda: asyncio.sleep(0))})()

    async def company_events(sec: Any, cik: int, _start: str, _end: str) -> list[dict[str, Any]]:
        calls[cik] = calls.get(cik, 0) + 1
        if cik == 3 or (cik == 2 and calls[cik] == 1):  # 3 never answers; 2 answers the second time
            sec.failed.add(cik)
            return []
        return [{"cik": cik, "accession": f"a{cik}", "filed": "2026-10-01", "accepted_utc": "2026-10-01T20:05:00",
                 "items": "2.02", "primary": "x.htm"}] if cik == 2 else []

    async def ex99_url(_sec: Any, e: dict[str, Any]) -> str:
        return f"u{e['cik']}"

    monkeypatch.setattr(build_events, "Sec", Sec)
    monkeypatch.setattr(build_events, "company_events", company_events)
    monkeypatch.setattr(build_events, "ex99_url", ex99_url)
    monkeypatch.setattr(FE, "_read_env_file", lambda _p: {"SEC_USER_AGENT": "test"})
    monkeypatch.setattr(FE, "members", lambda _y, _i=(): pd.DataFrame(
        {"cik": [1, 2, 3], "ticker": ["AAA", "BBB", "CCC"], "index": "sp500", "sector": IT}))
    found = asyncio.run(FE.discover(date(2026, 9, 28), datetime(2026, 10, 1, 22, 30, tzinfo=UTC)))
    assert list(found["ticker"]) == ["BBB"] and calls == {1: 1, 2: 2, 3: 2}
    assert FE.LOOKUPS == {"companies": 3, "failed_first": 2, "unread": ["CCC"]}
