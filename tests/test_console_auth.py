from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import agent.api.webhook as ws
from agent.auth.config import load_auth_config
from agent.auth.models import AuthUser


def test_healthz_is_public(monkeypatch) -> None:
    """Health check endpoint should be accessible without authentication."""
    monkeypatch.setenv("AUTH_SESSION_SECRET", "test-secret-key-for-testing-purposes-only")
    monkeypatch.setenv("ADMIN_INITIAL_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_INITIAL_PASSWORD", "password")
    load_auth_config.cache_clear()
    c = TestClient(ws.app)
    r = c.get("/healthz")
    assert r.status_code == 200


def test_auth_me_requires_auth(monkeypatch) -> None:
    """API endpoints require authentication (no disabled mode)."""
    monkeypatch.setenv("AUTH_SESSION_SECRET", "test-secret-key-for-testing-purposes-only")
    monkeypatch.setenv("ADMIN_INITIAL_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_INITIAL_PASSWORD", "password")
    load_auth_config.cache_clear()
    c = TestClient(ws.app)
    r = c.get("/api/auth/me")
    assert r.status_code == 401


def test_auth_me_no_www_authenticate_header(monkeypatch) -> None:
    """Auth endpoints should not set WWW-Authenticate to avoid browser auth popups."""
    monkeypatch.setenv("AUTH_SESSION_SECRET", "test-secret-key-for-testing-purposes-only")
    monkeypatch.setenv("ADMIN_INITIAL_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_INITIAL_PASSWORD", "password")
    load_auth_config.cache_clear()
    c = TestClient(ws.app)
    r = c.get("/api/auth/me")
    assert r.status_code == 401
    # We intentionally do NOT set WWW-Authenticate to avoid browser auth popups.
    assert "www-authenticate" not in {k.lower() for k in r.headers.keys()}


def test_auth_mode_returns_config(monkeypatch) -> None:
    """Auth mode endpoint should return configuration without authentication."""
    monkeypatch.setenv("AUTH_SESSION_SECRET", "test-secret-key-for-testing-purposes-only")
    monkeypatch.setenv("ADMIN_INITIAL_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_INITIAL_PASSWORD", "password")
    load_auth_config.cache_clear()
    c = TestClient(ws.app)
    r = c.get("/api/auth/mode")
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    assert body.get("localEnabled") is True
    assert body.get("oidcEnabled") is False


def test_auth_mode_with_oidc_enabled(monkeypatch) -> None:
    """Auth mode should detect OIDC when configured."""
    monkeypatch.setenv("AUTH_SESSION_SECRET", "test-secret-key-for-testing-purposes-only")
    monkeypatch.setenv("ADMIN_INITIAL_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_INITIAL_PASSWORD", "password")
    monkeypatch.setenv("OIDC_DISCOVERY_URL", "https://accounts.google.com/.well-known/openid-configuration")
    monkeypatch.setenv("OIDC_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "test-client-secret")
    load_auth_config.cache_clear()

    # Mock the OIDC discovery to avoid actual network call
    with patch("agent.auth.oidc._get_discovery") as mock_discovery:
        mock_discovery.return_value = {
            "issuer": "https://accounts.google.com",
            "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_endpoint": "https://oauth2.googleapis.com/token",
            "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
        }

        c = TestClient(ws.app)
        r = c.get("/api/auth/mode")
        assert r.status_code == 200
        body = r.json()
        assert body.get("ok") is True
        assert body.get("localEnabled") is True
        assert body.get("oidcEnabled") is True
        assert "oidcProvider" in body
        assert body["oidcProvider"]["name"] == "Google"


def test_local_login_missing_credentials(monkeypatch) -> None:
    """Local login should fail with 400 if credentials are missing."""
    monkeypatch.setenv("AUTH_SESSION_SECRET", "test-secret-key-for-testing-purposes-only")
    monkeypatch.setenv("ADMIN_INITIAL_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_INITIAL_PASSWORD", "password")
    load_auth_config.cache_clear()
    c = TestClient(ws.app)

    # Missing username
    r = c.post("/api/auth/login/local", json={"password": "test"})
    assert r.status_code == 400

    # Missing password
    r = c.post("/api/auth/login/local", json={"username": "test"})
    assert r.status_code == 400


def test_local_login_invalid_credentials(monkeypatch) -> None:
    """Local login should fail with 401 for invalid credentials."""
    monkeypatch.setenv("AUTH_SESSION_SECRET", "test-secret-key-for-testing-purposes-only")
    monkeypatch.setenv("ADMIN_INITIAL_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_INITIAL_PASSWORD", "password")
    load_auth_config.cache_clear()

    # Mock database connection to return None (user not found)
    with patch("agent.api.webhook._get_db_connection") as mock_db:
        mock_conn = MagicMock()
        mock_db.return_value = mock_conn

        with patch("agent.auth.local.authenticate_local") as mock_auth:
            mock_auth.return_value = None

            c = TestClient(ws.app)
            r = c.post("/api/auth/login/local", json={"username": "invalid", "password": "wrong"})
            assert r.status_code == 401


def test_local_login_success(monkeypatch) -> None:
    """Local login should succeed with valid credentials and set session cookie."""
    monkeypatch.setenv("AUTH_SESSION_SECRET", "test-secret-key-for-testing-purposes-only")
    monkeypatch.setenv("ADMIN_INITIAL_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_INITIAL_PASSWORD", "password")
    load_auth_config.cache_clear()

    # Mock database connection and authentication
    with patch("agent.api.webhook._get_db_connection") as mock_db:
        mock_conn = MagicMock()
        mock_db.return_value = mock_conn

        with patch("agent.auth.local.authenticate_local") as mock_auth:
            mock_auth.return_value = AuthUser(
                provider="local", email="admin@local", name="Admin", username="admin", picture=None
            )

            c = TestClient(ws.app)
            r = c.post("/api/auth/login/local", json={"username": "admin", "password": "password"})
            assert r.status_code == 200
            body = r.json()
            assert body.get("ok") is True
            assert body.get("user", {}).get("provider") == "local"
            assert body.get("user", {}).get("username") == "admin"

            # Session cookie should be set
            assert "set-cookie" in {k.lower() for k in r.headers.keys()}


def test_logout_clears_session(monkeypatch) -> None:
    """Logout should clear session cookie."""
    monkeypatch.setenv("AUTH_SESSION_SECRET", "test-secret-key-for-testing-purposes-only")
    monkeypatch.setenv("ADMIN_INITIAL_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_INITIAL_PASSWORD", "password")
    load_auth_config.cache_clear()
    c = TestClient(ws.app)
    r = c.post("/api/auth/logout")
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True

    # Cookie should be cleared (max-age=0)
    cookies = r.headers.get("set-cookie", "")
    assert "max-age=0" in cookies.lower() or "max_age=0" in cookies.lower()
