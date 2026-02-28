"""
Unit tests for k8s.events chat tool.
"""

from __future__ import annotations

import pytest

from agent.authz.policy import ChatPolicy
from agent.chat.tools import run_tool


class _MockK8sProvider:
    def __init__(self, events=None):
        self.events = events or []

    def get_events(self, *, namespace, resource_type=None, resource_name=None, limit=30):
        return self.events


@pytest.fixture
def mock_k8s_provider(monkeypatch):
    """Mock K8s provider with fake events."""
    events = [
        {
            "type": "Warning",
            "reason": "BackOff",
            "message": "Back-off restarting failed container",
            "count": 5,
            "last_timestamp": "2025-12-15T10:00:00Z",
            "involved_object": {"kind": "Pod", "name": "test-pod", "namespace": "default"},
        },
        {
            "type": "Normal",
            "reason": "Started",
            "message": "Started container",
            "count": 1,
            "last_timestamp": "2025-12-15T09:55:00Z",
            "involved_object": {"kind": "Pod", "name": "test-pod", "namespace": "default"},
        },
    ]

    provider = _MockK8sProvider(events=events)

    def _fake_get_k8s_provider():
        return provider

    # Patch both the module and the tools module import
    monkeypatch.setattr("agent.providers.k8s_provider.get_k8s_provider", _fake_get_k8s_provider)
    monkeypatch.setattr("agent.chat.tools.get_k8s_provider", _fake_get_k8s_provider)

    return provider


def test_k8s_events_requires_policy_flag():
    """Tool should be blocked if allow_k8s_events is False."""
    policy = ChatPolicy(allow_k8s_events=False)
    analysis = {"target": {"namespace": "default"}}

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="k8s.events",
        args={},
        analysis_json=analysis,
    )

    assert not result.ok
    assert result.error == "tool_not_allowed"


def test_k8s_events_requires_namespace(mock_k8s_provider):
    """Tool should require namespace."""
    policy = ChatPolicy(allow_k8s_events=True)
    analysis = {"target": {}}  # No namespace

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="k8s.events",
        args={},
        analysis_json=analysis,
    )

    assert not result.ok
    assert result.error == "namespace_required"


def test_k8s_events_namespace_wide(mock_k8s_provider):
    """Tool should fetch namespace-wide events when no resource specified."""
    policy = ChatPolicy(allow_k8s_events=True)
    analysis = {"target": {"namespace": "default"}}

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="k8s.events",
        args={},
        analysis_json=analysis,
    )

    assert result.ok
    assert isinstance(result.result, dict)
    assert result.result["namespace"] == "default"
    assert result.result["resource_type"] == "namespace-wide"
    assert result.result["resource_name"] == "all"
    assert len(result.result["events"]) == 2


def test_k8s_events_specific_pod(mock_k8s_provider):
    """Tool should fetch events for specific pod."""
    policy = ChatPolicy(allow_k8s_events=True)
    analysis = {"target": {"namespace": "default"}}

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="k8s.events",
        args={"resource_type": "pod", "resource_name": "test-pod"},
        analysis_json=analysis,
    )

    assert result.ok
    assert result.result["resource_type"] == "pod"
    assert result.result["resource_name"] == "test-pod"
    assert len(result.result["events"]) == 2


def test_k8s_events_defaults_to_target_pod(mock_k8s_provider):
    """Tool should default to investigation target pod if no resource specified."""
    policy = ChatPolicy(allow_k8s_events=True)
    analysis = {"target": {"namespace": "default", "pod": "target-pod"}}

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="k8s.events",
        args={},
        analysis_json=analysis,
    )

    assert result.ok
    assert result.result["resource_type"] == "pod"
    assert result.result["resource_name"] == "target-pod"


def test_k8s_events_defaults_to_target_workload(mock_k8s_provider):
    """Tool should default to workload if no pod in target."""
    policy = ChatPolicy(allow_k8s_events=True)
    analysis = {
        "target": {
            "namespace": "default",
            "workload_kind": "Deployment",
            "workload_name": "my-app",
        }
    }

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="k8s.events",
        args={},
        analysis_json=analysis,
    )

    assert result.ok
    assert result.result["resource_type"] == "deployment"
    assert result.result["resource_name"] == "my-app"


def test_k8s_events_limit_clamping(mock_k8s_provider):
    """Tool should clamp limit between 5-100."""
    policy = ChatPolicy(allow_k8s_events=True)
    analysis = {"target": {"namespace": "default"}}

    # Test limit < 5 (should clamp to 5)
    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="k8s.events",
        args={"limit": 1},
        analysis_json=analysis,
    )
    assert result.ok

    # Test limit > 100 (should clamp to 100)
    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="k8s.events",
        args={"limit": 500},
        analysis_json=analysis,
    )
    assert result.ok


def test_k8s_events_explicit_namespace_overrides_target(mock_k8s_provider):
    """Explicit namespace in args should override target namespace."""
    policy = ChatPolicy(allow_k8s_events=True)
    analysis = {"target": {"namespace": "old-ns"}}

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="k8s.events",
        args={"namespace": "new-ns"},
        analysis_json=analysis,
    )

    assert result.ok
    assert result.result["namespace"] == "new-ns"


def test_k8s_events_handles_provider_error(mock_k8s_provider, monkeypatch):
    """Tool should handle provider errors gracefully."""

    def _fake_get_events(**kwargs):
        raise Exception("K8s API unavailable")

    monkeypatch.setattr(mock_k8s_provider, "get_events", _fake_get_events)

    policy = ChatPolicy(allow_k8s_events=True)
    analysis = {"target": {"namespace": "default"}}

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="k8s.events",
        args={},
        analysis_json=analysis,
    )

    assert not result.ok
    assert "k8s_error" in result.error
