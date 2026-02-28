from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from agent.auth.models import AuthUser


class _FakeConn:
    def __init__(self, analysis_json: Any):
        self._analysis_json = analysis_json

    def execute(self, sql: str, params):  # type: ignore[no-untyped-def]
        _ = (sql, params)
        return self

    def fetchone(self):  # type: ignore[no-untyped-def]
        return (self._analysis_json,)

    def close(self) -> None:
        return None


def _mock_authenticated_user():
    """Mock an authenticated user for testing."""
    return AuthUser(provider="local", email="test@example.com", name="Test User", username="test")


def test_case_memory_endpoint_disabled_when_memory_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent.api.webhook as ws
    from agent.auth.config import load_auth_config

    class _Cfg:
        memory_enabled = False

    # Set up authentication
    monkeypatch.setenv("AUTH_SESSION_SECRET", "test-secret-key-for-testing-purposes-only")
    monkeypatch.setenv("ADMIN_INITIAL_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_INITIAL_PASSWORD", "password")
    load_auth_config.cache_clear()

    monkeypatch.setattr("agent.memory.config.load_memory_config", lambda: _Cfg())

    # Mock authentication to return a valid user
    with patch("agent.auth.deps.authenticate_request", return_value=_mock_authenticated_user()):
        c = TestClient(ws.app)
        r = c.get("/api/v1/cases/c1/memory")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["enabled"] is False


def test_case_memory_endpoint_returns_similar_and_skills(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent.api.webhook as ws
    from agent.auth.config import load_auth_config

    class _Cfg:
        memory_enabled = True

    # Set up authentication
    monkeypatch.setenv("AUTH_SESSION_SECRET", "test-secret-key-for-testing-purposes-only")
    monkeypatch.setenv("ADMIN_INITIAL_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_INITIAL_PASSWORD", "password")
    load_auth_config.cache_clear()

    monkeypatch.setattr("agent.memory.config.load_memory_config", lambda: _Cfg())

    analysis_json = {
        "alert": {"fingerprint": "fp", "labels": {"alertname": "X"}, "annotations": {}},
        "target": {"target_type": "pod", "namespace": "ns1", "cluster": "c1", "pod": "p1"},
        "analysis": {"features": {"family": "cpu_throttling"}},
    }

    monkeypatch.setattr(ws, "_get_db_connection", lambda: _FakeConn(analysis_json))

    class _Sim:
        def __init__(self) -> None:
            self.case_id = "c2"
            self.run_id = "r2"
            self.created_at = "t"
            self.one_liner = "one"
            self.s3_report_key = None
            self.resolution_category = "deploy"
            self.resolution_summary = "rolled back"
            self.postmortem_link = None

    def fake_find_similar_runs(_inv, limit: int = 5):  # type: ignore[no-untyped-def]
        return True, "ok", [_Sim()]

    class _Skill:
        name = "skill1"
        version = 1

    class _Match:
        skill = _Skill()
        rendered = "hello"
        match_reason = "matched"

    def fake_match_skills(_inv, max_matches: int = 5):  # type: ignore[no-untyped-def]
        return True, "ok", [_Match()]

    monkeypatch.setattr("agent.memory.case_retrieval.find_similar_runs", fake_find_similar_runs)
    monkeypatch.setattr("agent.memory.skills.match_skills", fake_match_skills)

    # Mock authentication to return a valid user
    with patch("agent.auth.deps.authenticate_request", return_value=_mock_authenticated_user()):
        c = TestClient(ws.app)
        r = c.get("/api/v1/cases/c1/memory?limit=5")
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is True
        assert body["errors"] == []
        assert body["similar_cases"] and body["similar_cases"][0]["case_id"] == "c2"
        assert body["skills"] and body["skills"][0]["name"] == "skill1"
