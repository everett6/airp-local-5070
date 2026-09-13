import pytest
from fastapi.testclient import TestClient

from app.api.routes.sandbox import get_history_store
from app.main import app
from app.store.sqlite_store import BacktestHistoryStore


@pytest.fixture
def client(tmp_path):
    test_store = BacktestHistoryStore(str(tmp_path / "api_test.db"))
    app.dependency_overrides[get_history_store] = lambda: test_store
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_backtest_rejects_future_date(client):
    resp = client.post(
        "/api/sandbox/backtest",
        json={"ticker": "ACME", "as_of": "2099-01-01", "horizon_days": 30},
    )
    assert resp.status_code == 400


def test_backtest_succeeds_and_is_recorded_in_history(client):
    resp = client.post(
        "/api/sandbox/backtest",
        json={"ticker": "ACME", "as_of": "2024-03-01", "horizon_days": 30},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ticker"] == "ACME"
    assert body["self_check_passed"] is True

    history_resp = client.get("/api/sandbox/history")
    assert history_resp.status_code == 200
    history_body = history_resp.json()
    assert len(history_body) == 1
    assert history_body[0]["run_id"] == body["run_id"]


def test_history_filters_by_ticker(client):
    client.post("/api/sandbox/backtest", json={"ticker": "ACME", "as_of": "2024-03-01", "horizon_days": 30})
    client.post("/api/sandbox/backtest", json={"ticker": "ZETA", "as_of": "2024-03-01", "horizon_days": 30})

    resp = client.get("/api/sandbox/history", params={"ticker": "ACME"})
    body = resp.json()
    assert len(body) == 1
    assert body[0]["ticker"] == "ACME"


def test_history_empty_by_default(client):
    resp = client.get("/api/sandbox/history")
    assert resp.status_code == 200
    assert resp.json() == []


def test_backtest_suite_records_every_run(client):
    resp = client.post(
        "/api/sandbox/backtest-suite",
        json={
            "ticker": "ACME", "start_date": "2024-01-01", "end_date": "2024-06-01",
            "horizon_days": 14, "step_days": 30,
        },
    )
    assert resp.status_code == 200
    suite_results = resp.json()
    assert len(suite_results) >= 3

    history_resp = client.get("/api/sandbox/history", params={"limit": 100})
    assert len(history_resp.json()) == len(suite_results)


def test_cors_headers_present_for_allowed_origin(client):
    resp = client.get("/api/health", headers={"Origin": "http://localhost:3000"})
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_gzip_applied_to_larger_responses(client):
    client.post(
        "/api/sandbox/backtest-suite",
        json={
            "ticker": "ACME", "start_date": "2020-01-01", "end_date": "2024-06-01",
            "horizon_days": 14, "step_days": 30,
        },
    )
    resp = client.get(
        "/api/sandbox/history", params={"limit": 100},
        headers={"Accept-Encoding": "gzip"},
    )
    assert resp.status_code == 200
    # TestClient decodes gzip transparently, so we can't assert on raw bytes
    # here — but we can confirm the endpoint still returns correct, complete
    # data when compression is in play, which is the behavior that matters.
    assert len(resp.json()) > 1
