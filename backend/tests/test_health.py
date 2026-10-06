"""
Tests for GET /health endpoint.

Per testing-with-discernment:
- Integration test (hits real DB through TestClient).
- Tests both "ok" and "degraded" responses.
- No mocking of the DB connection — that's the point: /health tells us if the
  DB is actually reachable.

TDD: tests written BEFORE app/main.py. Will fail with ImportError until implemented.
"""

import pytest
from unittest.mock import patch, AsyncMock


class TestHealth:

    @pytest.mark.integration
    def test_health_ok_when_db_reachable(self, test_db_url, monkeypatch):
        """Returns 200 {"status": "ok"} when Postgres responds to SELECT 1."""
        monkeypatch.setenv("DATABASE_URL", test_db_url)
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
        monkeypatch.setenv("APP_BASE_URL", "http://localhost:3000")

        from fastapi.testclient import TestClient
        from app.main import app

        with TestClient(app) as client:
            response = client.get("/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert "db" in body

    def test_health_degraded_when_db_unreachable(self, monkeypatch):
        """Returns 200 {"status": "degraded"} when DB connection fails."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:wrong@localhost:9999/no_such_db")
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
        monkeypatch.setenv("APP_BASE_URL", "http://localhost:3000")

        from fastapi.testclient import TestClient
        from app.main import app

        with TestClient(app) as client:
            response = client.get("/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "degraded"

    def test_health_has_no_sensitive_fields(self, test_db_url, monkeypatch):
        """Health response does not expose DATABASE_URL, credentials, or env vars."""
        monkeypatch.setenv("DATABASE_URL", test_db_url)
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
        monkeypatch.setenv("APP_BASE_URL", "http://localhost:3000")

        from fastapi.testclient import TestClient
        from app.main import app

        with TestClient(app) as client:
            response = client.get("/health")

        body_str = response.text
        assert "postgres" not in body_str
        assert "password" not in body_str.lower()
        assert test_db_url not in body_str


class TestCORS:

    def test_cors_allows_app_base_url(self, test_db_url, monkeypatch):
        """CORS allows requests from APP_BASE_URL."""
        monkeypatch.setenv("DATABASE_URL", test_db_url)
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
        monkeypatch.setenv("APP_BASE_URL", "http://localhost:3000")

        from fastapi.testclient import TestClient
        from app.main import app

        with TestClient(app) as client:
            response = client.options(
                "/health",
                headers={
                    "Origin": "http://localhost:3000",
                    "Access-Control-Request-Method": "GET",
                },
            )

        assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"

    def test_cors_blocks_unknown_origin(self, test_db_url, monkeypatch):
        """CORS does not reflect arbitrary origins."""
        monkeypatch.setenv("DATABASE_URL", test_db_url)
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
        monkeypatch.setenv("APP_BASE_URL", "http://localhost:3000")

        from fastapi.testclient import TestClient
        from app.main import app

        with TestClient(app) as client:
            response = client.get(
                "/health",
                headers={"Origin": "http://evil.com"},
            )

        # The response is given (health is public) but the CORS header
        # does not reflect the evil origin
        allow_origin = response.headers.get("access-control-allow-origin", "")
        assert allow_origin != "http://evil.com"
