"""E2E tests for authentication flows.

These tests require a running server and are executed in CI or manually.
Run with: pytest -m e2e
"""

import os
import time
from typing import Generator

import pytest
import requests

BASE_URL = "http://localhost:8080"
# Use environment variables for credentials (set in .env or CI)
ADMIN_USERNAME = os.getenv("ADMIN_INITIAL_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_INITIAL_PASSWORD", "admin123")

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def wait_for_server() -> Generator[None, None, None]:
    """Wait for server to be ready."""
    max_retries = 30
    for i in range(max_retries):
        try:
            r = requests.get(f"{BASE_URL}/healthz", timeout=2)
            if r.status_code == 200:
                break
        except requests.RequestException:
            if i == max_retries - 1:
                raise Exception("Server failed to start within 30 seconds")
            time.sleep(1)
    yield


def test_healthz_endpoint(wait_for_server):
    """Test health check endpoint is accessible."""
    r = requests.get(f"{BASE_URL}/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_auth_mode_endpoint(wait_for_server):
    """Test auth mode endpoint returns configuration."""
    r = requests.get(f"{BASE_URL}/api/auth/mode")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["localEnabled"] is True
    assert "oidcEnabled" in body


def test_auth_required_for_protected_endpoints(wait_for_server):
    """Test that protected endpoints require authentication."""
    r = requests.get(f"{BASE_URL}/api/auth/me")
    assert r.status_code == 401


def test_local_auth_login_flow(wait_for_server):
    """Test local username/password authentication."""
    # Login with admin credentials (from .env or defaults)
    r = requests.post(f"{BASE_URL}/api/auth/login/local", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"Login failed: {r.text}"
    body = r.json()
    assert body["ok"] is True
    assert body["user"]["provider"] == "local"
    assert body["user"]["username"] == ADMIN_USERNAME

    # Session cookie should be set
    cookies = r.cookies
    assert "tarka_session" in cookies

    # Use session to access protected endpoint
    r = requests.get(f"{BASE_URL}/api/auth/me", cookies=cookies)
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["provider"] == "local"
    assert body["user"]["username"] == ADMIN_USERNAME


def test_local_auth_invalid_credentials(wait_for_server):
    """Test login fails with invalid credentials."""
    r = requests.post(
        f"{BASE_URL}/api/auth/login/local", json={"username": ADMIN_USERNAME, "password": "wrongpassword"}
    )
    assert r.status_code == 401


def test_logout(wait_for_server):
    """Test logout clears session."""
    # Login first
    r = requests.post(f"{BASE_URL}/api/auth/login/local", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
    assert r.status_code == 200
    cookies = r.cookies

    # Logout
    r = requests.post(f"{BASE_URL}/api/auth/logout", cookies=cookies)
    assert r.status_code == 200
    # Use cookies from logout response (should have expired session cookie)
    logout_cookies = r.cookies

    # Session should be invalid after logout
    r = requests.get(f"{BASE_URL}/api/auth/me", cookies=logout_cookies)
    assert r.status_code == 401


def test_local_auth_case_retrieval_requires_auth(wait_for_server):
    """Test that case retrieval endpoints require authentication."""
    # Without auth
    r = requests.get(f"{BASE_URL}/api/cases")
    assert r.status_code == 401

    # With auth
    login_r = requests.post(
        f"{BASE_URL}/api/auth/login/local", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    )
    cookies = login_r.cookies

    # Should work with auth (even if no cases exist yet)
    r = requests.get(f"{BASE_URL}/api/cases", cookies=cookies)
    assert r.status_code in [200, 404], f"Unexpected status: {r.status_code}"
