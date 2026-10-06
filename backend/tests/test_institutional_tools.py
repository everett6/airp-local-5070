"""Attribution, explicit human review, structured failures and scratch restoration. No live calls."""
from __future__ import annotations

import gzip
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ops_tool as ops

from app.forward.benchmark_review import attest, materials
from app.forward.ledger import Ledger
from app.forward.step_result import outcome
from app.portfolio.measurement import execution, factors, preserve_closes


def test_execution_uses_cumulative_fills_signed_arrival_and_explicit_costs():
    arrival = {"type": "risk", "allowed": True, "symbol": "SPY", "mid": 100, "spread_bp": 2,
               "quote_at": "2026-10-02T15:00:00Z", "quote_age_s": 0}
    rows = [{**arrival, "client_order_id": "b", "side": "buy"},
            {**arrival, "client_order_id": "s", "side": "sell"},
            {"type": "submission", "client_order_id": "b", "latency_ms": 50},
            {"type": "fill_snapshot", "client_order_id": "b", "filled_qty": 1, "filled_price": 101},
            {"type": "fill_snapshot", "client_order_id": "b", "filled_qty": 2, "filled_price": 101},
            {"type": "fill_snapshot", "client_order_id": "s", "filled_qty": 3, "filled_price": 99}]
    result = execution(rows, {"fees": 1, "borrow": 2, "financing": 3})
    assert result["fills_measured"] == 2 and result["notional"] == 499
    assert result["weighted_slippage_bp"] == pytest.approx(100)
    assert result["period_cost_dollars"]["arrival_shortfall"] == 5
    assert result["period_cost_dollars"]["total"] == 11
    assert execution(rows)["period_cost_dollars"] is None
    with pytest.raises(ValueError):
        execution(rows, {"fees": -1, "borrow": 0, "financing": 0})


def test_factor_alignment_no_forward_fill_exact_loadings_and_collinearity():
    dates = pd.date_range("2020-01-01", periods=300)
    rng = np.random.default_rng(127)
    market = pd.DataFrame(rng.normal(0, 0.02, (300, 2)), index=dates, columns=["SPY", "BTC"])
    returns = 0.001 + market.SPY * 0.8 + market.BTC * 0.2
    market.loc[dates[100], "SPY"] = np.nan
    result = factors(returns, market)
    assert result["observations"] == 299 and result["status"] == "estimated"
    assert result["loadings"] == pytest.approx({"SPY": 0.8, "BTC": 0.2})
    assert result["intercept_per_period"] == pytest.approx(.001)
    assert factors(returns.head(3), market)["status"] == "insufficient_data"
    market["duplicate"] = market.BTC
    assert factors(returns, market)["status"] == "collinear_factors"


def test_hac_uncertainty_accounts_for_persistent_residuals():
    rng = np.random.default_rng(71)
    dates = pd.date_range("2020-01-01", periods=1200)
    market = pd.DataFrame({"SPY": rng.normal(0, .02, len(dates))}, index=dates)
    errors = rng.normal(0, .005, len(dates))
    for n in range(1, len(errors)):
        errors[n] += .8 * errors[n - 1]
    returns = pd.Series(errors, index=dates) + .5 * market.SPY
    hac, iid = factors(returns, market), factors(returns, market, lag=0)
    assert hac["intercept_95"][1] - hac["intercept_95"][0] > 1.5 * (iid["intercept_95"][1] - iid["intercept_95"][0])
    assert hac["intercept_95"][0] < 0 < hac["intercept_95"][1]


def test_attribution_cache_uses_completed_closes_without_mutating_live_prices(tmp_path):
    closes = pd.DataFrame({"SPY": [100., 101., 999.], "BTC-USD": [200., 201., 888.]},
                          index=pd.date_range("2026-10-01", periods=3))
    original = closes.copy()
    path = tmp_path / "closes.parquet"
    preserve_closes(path, closes, datetime(2026, 10, 3, tzinfo=UTC))
    saved = pd.read_parquet(path)
    assert len(saved) == 2 and saved.SPY.iloc[-1] == 101
    pd.testing.assert_frame_equal(closes, original)


def test_report_aligns_crypto_weekends_and_missing_equity_marks_to_stock_sessions(tmp_path):
    from institutional_report import attribution
    days = pd.bdate_range("2025-01-01", periods=160)
    calendar = pd.date_range(days[0], days[-1])
    rng = np.random.default_rng(90)
    stock = pd.Series(100 * np.cumprod(1 + rng.normal(0, .01, len(days))), index=days)
    coin = pd.Series(100 * np.cumprod(1 + rng.normal(0, .02, len(calendar))), index=calendar)
    paired = .001 + .7 * stock.pct_change() + .3 * coin.reindex(days).pct_change()
    equity = 10000 * (1 + paired.fillna(0)).cumprod()
    equity = equity.drop(days[70])
    book = tmp_path / "results/forward/ai_picks/book.json"
    book.parent.mkdir(parents=True)
    book.write_text(json.dumps({"history": [{"day": str(d.date()), "equity": v} for d, v in equity.items()]}))
    stock_path = tmp_path / "data/trend/etf_closes.parquet"
    stock_path.parent.mkdir(parents=True)
    stock.to_frame("SPY").to_parquet(stock_path)
    coin_path = tmp_path / "data/crypto/ohlcv_crypto.parquet"
    coin_path.parent.mkdir(parents=True)
    pd.DataFrame({"Date": calendar, "Ticker": "BTC-USD", "Close": coin.values}).to_parquet(coin_path)
    result = attribution(tmp_path)
    assert result["observations"] == 157 and result["status"] == "estimated"
    assert result["loadings"] == pytest.approx({"SPY": .7, "BTC-USD": .3})
    assert result["intercept_per_period"] == pytest.approx(.001)


def benchmark(tmp_path):
    folder = tmp_path / "benchmarks/extraction"
    folder.mkdir(parents=True)
    case = {"accession": "0001-26-01", "ticker": "AAA", "revenue": {"q": 4, "prior": None}}
    (folder / "gold.json").write_text(json.dumps({"version": 1, "cases": [case]}))
    source = tmp_path / "data/events/text/0001-26-01.txt.gz"
    source.parent.mkdir(parents=True)
    with gzip.open(source, "wt") as stream:
        stream.write("Quarter revenue $4 million; no prior comparison disclosed")
    m = materials(tmp_path)[0]
    kwargs = {"accession": case["accession"], "reviewer": "Human test fixture", "checks": ["period", "units", "basis", "absence"],
              "decision": "approved", "note": "Synthetic labels verified", "expected_source_hash": m["source_hash"], "expected_case_hash": m["case_hash"]}
    return source, kwargs


def test_review_requires_explicit_person_checks_and_source_hash(tmp_path):
    source, kwargs = benchmark(tmp_path)
    assert not materials(tmp_path)[0]["approved"]
    with pytest.raises(ValueError):
        attest(tmp_path, **(kwargs | {"checks": ["period"]}))
    with pytest.raises(ValueError):
        attest(tmp_path, **(kwargs | {"expected_case_hash": "stale"}))
    attest(tmp_path, **kwargs)
    assert materials(tmp_path)[0]["approved"]
    with gzip.open(source, "wt") as stream:
        stream.write("Revised source")
    assert not materials(tmp_path)[0]["approved"]
    with pytest.raises(ValueError):
        attest(tmp_path, **kwargs)


def test_review_corrections_cannot_delete_fields_or_smuggle_nan(tmp_path):
    _, kwargs = benchmark(tmp_path)
    with pytest.raises(ValueError):
        attest(tmp_path, **kwargs, corrected={"accession": kwargs["accession"], "ticker": "AAA"})
    case = materials(tmp_path)[0]["case"]
    case["revenue"]["q"] = float("nan")
    with pytest.raises(ValueError):
        attest(tmp_path, **kwargs, corrected=case)
    case = materials(tmp_path)[0]["case"]
    case["revenue"]["q"] = 4
    # Named traps are scored numerically and must not poison all later reports.
    case["revenue"]["traps"] = {"full_year": "4 million"}
    gold = tmp_path / "benchmarks/extraction/gold.json"
    original = json.loads(gold.read_text())
    original["cases"][0]["revenue"]["traps"] = {"full_year": 12}
    gold.write_text(json.dumps(original))
    kwargs["expected_case_hash"] = materials(tmp_path)[0]["case_hash"]
    with pytest.raises(ValueError, match="finite numbers"):
        attest(tmp_path, **kwargs, corrected=case)


def test_structured_failures_override_success_exit_and_legacy_stays_unknown():
    r = outcome(["test"], 0, 'AIRP_RESULT {"version":1,"status":"failed"}', "", 1)
    assert r["status"] == "failed" and r["protocol"] == "native"
    assert outcome(["test"], 0, "looks good", "", 1)["status"] == "unknown"
    assert outcome(["test"], 1, 'AIRP_RESULT {"version":1,"status":"ok"}', "", 1)["status"] == "failed"
    assert outcome(["test"], 0, 'AIRP_RESULT []', "", 1)["protocol"] == "legacy"


def test_verified_backup_restore_never_overwrites_production_and_detects_tampering(tmp_path):
    folder = tmp_path / "backend/results/forward"
    folder.mkdir(parents=True)
    Ledger(folder / "ledger.jsonl").append("decision", ticker="AAA")
    (folder / ".env.private").write_text("synthetic secret")
    (folder / "symlink").symlink_to(folder / ".env.private")
    saved = ops.bundle(tmp_path, "backup")
    assert set(saved["files"]) == {"backend/results/forward/ledger.jsonl"}
    original = (folder / "ledger.jsonl").read_bytes()
    restored = ops.verify_restore(tmp_path)
    assert (folder / "ledger.jsonl").read_bytes() == original
    assert Ledger(Path(restored["path"]) / "backend/results/forward/ledger.jsonl").verify()[0]["ticker"] == "AAA"
    archive = tmp_path / "backend/results/ops/backups" / saved["id"] / "snapshot.tar.gz"
    archive.write_bytes(archive.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="digest"):
        ops.verify_restore(tmp_path)


def test_release_fingerprint_changes_when_candidate_source_changes(tmp_path):
    p = tmp_path / "backend/app"
    p.mkdir(parents=True)
    (p / "rule.py").write_text("version=1")
    before = ops.fingerprint(tmp_path)
    (p / "rule.py").write_text("version=2")
    assert before != ops.fingerprint(tmp_path)


def test_cost_review_is_bound_to_ledger_and_release_review_to_preserved_candidate(tmp_path):
    from engineering_review import review
    backend = tmp_path / "backend"
    folder = backend / "results/forward/execution"
    folder.mkdir(parents=True)
    Ledger(folder / "ledger.jsonl").append("risk", allowed=False, reasons=["limit"])
    data = {"action": "costs", "confirm": "REVIEWED", "reviewer": "Synthetic reviewer", "note": "Synthetic statement",
            "digest": ops.sha(folder / "ledger.jsonl"), "costs": {"fees": 1, "borrow": 2, "financing": 3}}
    review(backend, data)
    assert json.loads((folder / "costs.json").read_text())["costs"]["fees"] == 1
    Ledger(folder / "ledger.jsonl").append("risk", allowed=False, reasons=["another limit"])
    with pytest.raises(ValueError, match="changed"):
        review(backend, data)
    source = backend / "app/example.py"
    source.parent.mkdir()
    source.write_text("VERSION=1")
    candidate = ops.bundle(tmp_path, "release")
    release = {**data, "action": "release", "digest": candidate["archive_sha256"], "checks": ["source", "tests", "risk", "recovery"]}
    review(backend, release)
    assert Ledger(backend / "results/ops/reviews.jsonl").verify()[0]["candidate"] == candidate["id"]
    with pytest.raises(ValueError):
        review(backend, release | {"checks": ["tests"]})
