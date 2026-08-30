import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app.common.config import CORS_ORIGINS
from app.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "UP"}


def test_cors_preflight_allows_configured_origin() -> None:
    response = client.options(
        "/health",
        headers={
            "Origin": CORS_ORIGINS[0],
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == CORS_ORIGINS[0]
    assert response.headers["access-control-allow-credentials"] == "true"


def test_cors_preflight_rejects_unconfigured_origin() -> None:
    response = client.options(
        "/health",
        headers={
            "Origin": "https://not-allowed.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_health_import_succeeds_without_app_database_credential() -> None:
    """DB secret 부재가 import·liveness를 막지 않고 첫 DB 사용에서만 실패한다."""

    env = os.environ.copy()
    env["APP_DATABASE_URL"] = ""
    env["APP_DB_PASSWORD"] = ""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from fastapi.testclient import TestClient; "
                "from app.main import app; "
                "response = TestClient(app).get('/health'); "
                "assert response.status_code == 200; "
                "assert response.json() == {'status': 'UP'}"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd=Path(__file__).resolve().parents[1],
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
