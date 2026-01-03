import os
from uuid import uuid4

from fastapi.testclient import TestClient

# Unit tests should not require PostgreSQL (or psycopg2) unless explicitly requested.
os.environ.pop("AUTOTRADER_DATABASE_URL", None)
os.environ.pop("DATABASE_URL", None)

# Unit tests should not require IBKR to be running.
os.environ.setdefault("AUTOTRADER_DISABLE_IBKR_SERVICE", "1")

from src.api.app import app
from src.utils.database import init_db, log_event, update_performance


def test_api_health_ok():
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "db_path" in data


def test_api_events_contains_recent_event():
    init_db()
    token = f"pytest-api-{uuid4()}"
    log_event(level="INFO", message=token, symbol="TEST", step="pytest")

    client = TestClient(app)
    resp = client.get("/api/history/events", params={"limit": 50})
    assert resp.status_code == 200
    events = resp.json()
    assert any(e.get("message") == token for e in events)


def test_api_performance_summary_returns_latest_equity():
    init_db()
    # Insert a couple of rows; latest should reflect the last write.
    update_performance(equity=12345.0, unrealized_pnl=0.0, realized_pnl=0.0)
    update_performance(equity=23456.0, unrealized_pnl=0.0, realized_pnl=0.0)

    client = TestClient(app)
    resp = client.get("/api/history/performance/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("latest_equity") == 23456.0


