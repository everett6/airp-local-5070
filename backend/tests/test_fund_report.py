"""Fund attribution and execution reports: synthetic records, no broker or result writes."""
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from app.forward.ledger import Ledger
from app.portfolio.broker import PAPER, Alpaca, BrokerError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fund_report as F


class FakeAlpaca:
    def __init__(self, equity: str | BrokerError = "1000") -> None:
        self.equity = equity

    def _get(self, url: str, **params: Any) -> Any:
        if url == f"{PAPER}/account/activities":
            return [{"order_id": "1", "qty": "2", "price": "101", "side": "buy"},
                    {"order_id": "1", "qty": "3", "price": "99", "side": "buy"},
                    {"order_id": "2", "qty": "4", "price": "50", "side": "sell"}]
        if url == f"{PAPER}/orders":
            return [{"id": "1", "client_order_id": "fa-one"},
                    {"id": "2", "client_order_id": "ae-two"}]
        assert url == f"{PAPER}/account"
        if isinstance(self.equity, BrokerError):
            raise self.equity
        return {"equity": self.equity}


def test_regimes_reads_decisions_and_ignores_torn_lines(tmp_path: Path) -> None:
    path = tmp_path / "thoughts.jsonl"
    path.write_text(
        '{"type": "decision", "session": "2026-10-05", "regime": "bull"}\n'
        '{"kind": "decision", "session": "2026-10-06", "regime": "bear"}\n'
        '{"type": "other", "session": "2026-10-05", "regime": "bear"}\n'
        '{"type": "decision", "session": "2026-10-05", "regime": "neutral"}\n'
        '{"type": "decision", "session": "2026-10-07", "regime": null}\n'
        '[]\n{"type":'
    )
    assert F.regimes(path) == {"2026-10-05": "neutral", "2026-10-06": "bear"}
    assert F.regimes(tmp_path / "missing") == {}


def test_calls_split_by_entry_session_regime_with_unknown_fallback() -> None:
    recs = [{"ticker": "T", "status": "decided", "decided_at": t, "ratings": {"day": 4}}
            for t in ("2026-10-02T16:00:00-04:00", "2026-10-06T08:00:00-04:00",
                      "2026-10-07T08:00:00-04:00")]
    px = {"T": {d: (100, close) for d, close in
                (("2026-10-05", 110), ("2026-10-06", 90), ("2026-10-07", 105))},
          "QQQ": {d: (100, 100) for d in ("2026-10-05", "2026-10-06", "2026-10-07")}}
    result = F.evaluate_calls(recs, px, regime_by_session={"2026-10-05": "bull",
                                                         "2026-10-06": "bear"})
    assert result["all"]["n"] == 3
    groups = result["by_regime"]
    assert {k: v["n"] for k, v in groups.items()} == {"bull": 1, "bear": 1, "unknown": 1}
    assert abs(groups["bull"]["mean"] - 0.1) < 1e-12
    assert abs(groups["bear"]["mean"] + 0.1) < 1e-12
    assert F.evaluate_calls(recs, px)["by_regime"]["unknown"]["n"] == 3


def test_turnover_sums_every_fill_and_reports_absent_quotes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(F, "FWD", tmp_path)
    Ledger(tmp_path / "full_auto" / "execution" / "ledger.jsonl").append(
        "risk", client_order_id="fa-one", ref_price=100,
    )
    result = F.execution(cast(Alpaca, FakeAlpaca()))
    assert result["fills"] == 3
    assert result["turnover_notional"] == {"fa": 499, "ae": 200}
    assert result["turnover_total"] == 699
    assert abs(result["turnover_equity_multiple"] - 0.699) < 1e-12
    assert result["spread_paid_bp"] == {}
    assert result["spread_note"] == "quotes not recorded on the risk lines"
    assert result["slippage_bp"]["fa"]["n"] == 2
    assert abs(result["slippage_bp"]["fa"]["mean"]) < 1e-12


@pytest.mark.parametrize("kind", ["type", "kind"])
def test_spread_uses_quotes_then_recorded_spread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    import json

    monkeypatch.setattr(F, "FWD", tmp_path)
    path = tmp_path / "algo" / "execution" / "ledger.jsonl"
    path.parent.mkdir(parents=True)
    rows = [{kind: "risk", "client_order_id": "fa-one", "ref_price": 100,
             "bid": 99, "ask": 101, "spread_bp": 999},
            {kind: "risk", "client_order_id": "ae-two", "spread_bp": 20},
            {kind: "other", "client_order_id": "ae-two", "spread_bp": 999}]
    path.write_text('\n'.join(json.dumps(r) for r in rows) + '\n{"kind":')
    result = F.execution(cast(Alpaca, FakeAlpaca()))
    assert result["spread_paid_bp"] == {"fa": {"n": 2, "mean": 100},
                                        "ae": {"n": 1, "mean": 10}}
    assert "spread_note" not in result


@pytest.mark.parametrize("equity", ["0", BrokerError("account unavailable")])
def test_equity_unavailable_keeps_turnover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, equity: str | BrokerError,
) -> None:
    monkeypatch.setattr(F, "FWD", tmp_path)
    result = F.execution(cast(Alpaca, FakeAlpaca(equity)))
    assert result["turnover_total"] == 699
    assert result["turnover_equity_multiple"] is None
