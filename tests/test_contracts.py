import os
import socket
import subprocess
import sys
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.utils.database import get_events, init_db, log_event

pytestmark = pytest.mark.integration

# Ensure we don't accidentally disable IBKR in integration runs.
os.environ.pop("AUTOTRADER_DISABLE_IBKR_SERVICE", None)


def _require_postgres() -> None:
    url = (os.environ.get("AUTOTRADER_DATABASE_URL") or os.environ.get("DATABASE_URL") or "").strip()
    if not (url.startswith("postgres://") or url.startswith("postgresql://")):
        pytest.fail(
            "These contract tests require PostgreSQL. Set AUTOTRADER_DATABASE_URL to a postgresql://... DSN and retry."
        )

    # Fail fast if PostgreSQL is not reachable (avoid hanging test runs).
    try:
        from urllib.parse import urlparse

        u = urlparse(url)
        safe_url = f"{u.scheme}://{u.hostname or 'localhost'}:{u.port or 5432}/{(u.path or '/').lstrip('/') or 'postgres'}"
    except Exception:
        safe_url = "postgresql://<redacted>"

    try:
        import psycopg2  # type: ignore

        # connect_timeout is also enforced by the DB layer, but we keep it explicit here.
        conn = psycopg2.connect(url, connect_timeout=3)
        conn.close()
    except Exception as e:
        pytest.fail(f"PostgreSQL not reachable at {safe_url}: {type(e).__name__}: {str(e)[:200]}")

def _require_ib_insync() -> None:
    try:
        import ib_insync  # noqa: F401
    except Exception:
        pytest.fail(
            "ib_insync is not available in the current Python environment. "
            "Run tests under the 'autotrader' conda env, e.g. `conda run -n autotrader pytest`."
        )


@pytest.mark.order(1)
def test_history_endpoints_ok():
    _require_postgres()
    _require_ib_insync()
    from src.api.app import app
    client = TestClient(app)

    health = client.get("/api/health")
    assert health.status_code == 200
    data = health.json()
    assert data.get("db_ok") is True

    # History endpoints should be DB-backed and stable.
    resp = client.get("/api/history/events", params={"limit": 10})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

    resp = client.get("/api/history/research", params={"limit": 10})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

    resp = client.get("/api/history/trades", params={"limit": 10})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

    resp = client.get("/api/history/performance")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.order(2)
def test_sse_stream_emits_valid_json_event():
    _require_postgres()
    _require_ib_insync()
    init_db()
    token = f"pytest-sse-{uuid4()}"

    # Stream only NEW events: get the current latest id, then write our marker event.
    df = get_events(limit=1)
    last_id = int(df.iloc[0]["id"]) if df is not None and not df.empty else 0
    log_event(level="INFO", message=token, symbol="TEST", step="pytest-sse")

    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])

    port = _free_port()

    # Run a real uvicorn process so SSE is tested as actual streaming over HTTP.
    env = os.environ.copy()
    env["AUTOTRADER_API_RELOAD"] = "0"
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "src.api.app:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
        "--workers",
        "1",
    ]

    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        import httpx

        # Wait for server to come up (max ~8s).
        deadline = time.monotonic() + 8.0
        while True:
            if proc.poll() is not None:
                pytest.fail("uvicorn exited early; SSE smoke test cannot proceed")
            try:
                r = httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1.0)
                if r.status_code == 200:
                    break
            except Exception:
                pass
            if time.monotonic() > deadline:
                pytest.fail("uvicorn did not become healthy within the startup window")
            time.sleep(0.2)

        # Now connect SSE and verify we can observe our marker event quickly.
        found = False
        with httpx.stream(
            "GET",
            f"http://127.0.0.1:{port}/api/events/stream?after_id={last_id}&poll_seconds=0.2",
            timeout=httpx.Timeout(connect=2.0, read=5.0, write=2.0, pool=2.0),
        ) as resp:
            assert resp.status_code == 200
            start = time.monotonic()
            for line in resp.iter_lines():
                if (time.monotonic() - start) > 6.0:
                    break
                if not line:
                    continue
                if line.startswith("retry:") or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue

                payload = line[len("data:") :].strip()
                try:
                    import json

                    data = json.loads(payload)
                except Exception:
                    continue

                if data.get("message") == token:
                    assert isinstance(data.get("id"), int)
                    assert isinstance(data.get("timestamp"), str)
                    assert data.get("symbol") == "TEST"
                    found = True
                    break

        assert found, "Did not observe the expected SSE event payload within the read window"
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


@pytest.mark.order(3)
def test_live_endpoints_require_ibkr_and_return_200_when_connected():
    """
    These endpoints are IBKR-backed by design.
    If IBKR is not connected/ready, they must return 503 (no DB fallbacks).
    """
    _require_postgres()
    _require_ib_insync()
    from src.api.app import app
    client = TestClient(app)

    resp = client.get("/api/live/account-summary")
    if resp.status_code == 503:
        pytest.fail(
            "IBKR live endpoint returned 503. Start TWS/Gateway in PAPER trading mode, "
            "enable API access, and ensure broker.host/port in config.yaml are correct."
        )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

    resp = client.get("/api/live/positions")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

    resp = client.get("/api/live/open-orders")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.order(4)
def test_live_endpoints_return_503_when_service_stopped():
    _require_postgres()
    _require_ib_insync()
    import src.api.app as app_module
    from src.api.app import app

    client = TestClient(app)
    # Stop the live service, then assert live endpoints fail loudly.
    if getattr(app_module, "_ibkr_service", None) is not None:
        try:
            app_module._ibkr_service.stop()
        except Exception:
            pass
        app_module._ibkr_service = None

    resp = client.get("/api/live/account-summary")
    assert resp.status_code == 503


