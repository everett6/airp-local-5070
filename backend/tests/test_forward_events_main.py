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
