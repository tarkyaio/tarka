"""Tests for rerun.investigation chat tool."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from agent.authz.policy import ChatPolicy
from agent.chat.tools import run_tool


@pytest.fixture
def mock_policy():
    """Chat policy with report rerun enabled."""
    return ChatPolicy(
        enabled=True,
        allow_report_rerun=True,
        max_time_window_seconds=7200,  # 2 hours
    )


@pytest.fixture
def mock_analysis_json():
    """Minimal investigation context for rerun."""
    return {
        "alert": {
            "fingerprint": "test-fp-123",
            "labels": {
                "alertname": "PodCPUThrottling",
                "namespace": "default",
                "pod": "my-app-pod",
            },
            "annotations": {},
            "starts_at": "2026-02-19T10:00:00Z",
        },
        "target": {
            "namespace": "default",
            "pod": "my-app-pod",
        },
        "time_window": {
            "window": "1h",
            "start_time": "2026-02-19T09:00:00Z",
            "end_time": "2026-02-19T10:00:00Z",
        },
    }


def test_rerun_investigation_requires_time_window(mock_policy, mock_analysis_json):
    """Test that rerun.investigation requires time_window parameter."""
    # Call without time_window parameter
    result = run_tool(
        tool="rerun.investigation",
        args={},  # Empty args - missing time_window
        policy=mock_policy,
        action_policy=None,
        analysis_json=mock_analysis_json,
    )

    # Should fail with time_window_required error
    assert result.ok is False
    assert result.error == "time_window_required"


def test_rerun_investigation_with_valid_time_window(mock_policy, mock_analysis_json):
    """Test that rerun.investigation works with valid time_window."""
    with patch("agent.chat.tools.run_investigation") as mock_run:
        # Mock investigation result
        mock_investigation = MagicMock()
        mock_investigation.time_window.start_time = datetime(2026, 2, 19, 9, 30, 0, tzinfo=timezone.utc)
        mock_investigation.time_window.end_time = datetime(2026, 2, 19, 10, 0, 0, tzinfo=timezone.utc)
        mock_investigation.time_window.window = "30m"
        mock_run.return_value = mock_investigation

        with patch("agent.chat.tools.investigation_to_json_dict") as mock_json:
            mock_json.return_value = {
                "analysis": {
                    "verdict": {"label": "Updated verdict"},
                }
            }

            # Call with valid time_window
            result = run_tool(
                tool="rerun.investigation",
                args={"time_window": "30m"},
                policy=mock_policy,
                action_policy=None,
                analysis_json=mock_analysis_json,
            )

            # Should succeed
            assert result.ok is True
            assert result.result["status"] == "ok"
            assert result.updated_analysis is not None

            # Verify run_investigation was called
            mock_run.assert_called_once()


def test_rerun_investigation_rejects_too_large_window(mock_policy, mock_analysis_json):
    """Test that rerun.investigation rejects time windows exceeding policy max."""
    with patch("agent.chat.tools.run_investigation") as mock_run:
        # Mock investigation with large time window (exceeds 2h policy limit)
        mock_investigation = MagicMock()
        mock_investigation.time_window.start_time = datetime(2026, 2, 19, 6, 0, 0, tzinfo=timezone.utc)
        mock_investigation.time_window.end_time = datetime(2026, 2, 19, 10, 0, 0, tzinfo=timezone.utc)
        mock_investigation.time_window.window = "4h"
        mock_run.return_value = mock_investigation

        # Call with large window
        result = run_tool(
            tool="rerun.investigation",
            args={"time_window": "4h"},
            policy=mock_policy,
            action_policy=None,
            analysis_json=mock_analysis_json,
        )

        # Should fail with time_window_too_large
        assert result.ok is False
        assert result.error == "time_window_too_large"


def test_rerun_investigation_tool_description_includes_parameters():
    """Test that the tool description mentions required parameters."""
    from agent.chat.runtime import TOOL_DESCRIPTIONS

    description = TOOL_DESCRIPTIONS.get("rerun.investigation")

    # Verify description exists
    assert description is not None

    # Verify description mentions time_window parameter
    assert "time_window" in description.lower()

    # Verify description indicates it's required
    assert "required" in description.lower() or "args:" in description.lower()

    # Verify description provides examples
    assert any(example in description for example in ["30m", "1h", "2h"])


def test_rerun_investigation_disabled_by_policy():
    """Test that rerun.investigation is blocked when policy disables it."""
    disabled_policy = ChatPolicy(
        enabled=True,
        allow_report_rerun=False,  # Disabled
    )

    analysis_json = {
        "alert": {"fingerprint": "test", "labels": {}, "annotations": {}},
        "target": {},
    }

    result = run_tool(
        tool="rerun.investigation",
        args={"time_window": "1h"},
        policy=disabled_policy,
        action_policy=None,
        analysis_json=analysis_json,
    )

    # Should fail with tool_not_allowed
    assert result.ok is False
    assert result.error == "tool_not_allowed"


def test_rerun_investigation_preserves_original_alert_timestamp(mock_policy):
    """Test that rerun uses original alert timestamp by default (historical mode).

    Critical: When reruns happen, the time window should be relative to when
    the alert originally fired, not the current time. This ensures consistent
    investigation windows.
    """
    original_alert_time = "2026-02-19T10:00:00Z"

    analysis_json = {
        "alert": {
            "fingerprint": "test-fp-123",
            "labels": {
                "alertname": "PodCPUThrottling",
                "namespace": "default",
                "pod": "my-app-pod",
            },
            "annotations": {},
            "starts_at": original_alert_time,  # Original alert time (in the past)
            "ends_at": "0001-01-01T00:00:00Z",
            "generator_url": "http://prometheus:9090/graph",
            "state": "firing",
        },
        "target": {
            "namespace": "default",
            "pod": "my-app-pod",
        },
        "time_window": {
            "window": "1h",
            "start_time": "2026-02-19T09:00:00Z",
            "end_time": "2026-02-19T10:00:00Z",
        },
    }

    with patch("agent.chat.tools.run_investigation") as mock_run:
        # Mock investigation result
        mock_investigation = MagicMock()
        mock_investigation.time_window.start_time = datetime(2026, 2, 19, 9, 30, 0, tzinfo=timezone.utc)
        mock_investigation.time_window.end_time = datetime(2026, 2, 19, 10, 0, 0, tzinfo=timezone.utc)
        mock_investigation.time_window.window = "30m"
        mock_run.return_value = mock_investigation

        with patch("agent.chat.tools.investigation_to_json_dict") as mock_json:
            mock_json.return_value = {"analysis": {}}

            # Call rerun with 30m window (default: reference_time="original")
            result = run_tool(
                tool="rerun.investigation",
                args={"time_window": "30m"},
                policy=mock_policy,
                action_policy=None,
                analysis_json=analysis_json,
            )

            # Should succeed
            assert result.ok is True

            # Verify run_investigation was called with original alert timestamp
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            alert_passed = call_args[1]["alert"]  # kwargs["alert"]

            # Critical assertion: starts_at should match original alert time
            assert alert_passed["starts_at"] == original_alert_time
            assert alert_passed["fingerprint"] == "test-fp-123"
            assert alert_passed["labels"]["alertname"] == "PodCPUThrottling"

            # Verify time window is relative to original alert, not "now"
            # This ensures evidence collection looks at historical data from when alert fired
            time_window_passed = call_args[1]["time_window"]
            assert time_window_passed == "30m"


def test_rerun_investigation_with_reference_time_original(mock_policy, mock_analysis_json):
    """Test explicit reference_time='original' mode (historical investigation)."""
    with patch("agent.chat.tools.run_investigation") as mock_run:
        mock_investigation = MagicMock()
        mock_investigation.time_window.start_time = datetime(2026, 2, 19, 9, 0, 0, tzinfo=timezone.utc)
        mock_investigation.time_window.end_time = datetime(2026, 2, 19, 10, 0, 0, tzinfo=timezone.utc)
        mock_investigation.time_window.window = "1h"
        mock_run.return_value = mock_investigation

        with patch("agent.chat.tools.investigation_to_json_dict") as mock_json:
            mock_json.return_value = {"analysis": {}}

            result = run_tool(
                tool="rerun.investigation",
                args={"time_window": "1h", "reference_time": "original"},
                policy=mock_policy,
                action_policy=None,
                analysis_json=mock_analysis_json,
            )

            assert result.ok is True
            alert_passed = mock_run.call_args[1]["alert"]
            # Should use original alert timestamp
            assert alert_passed["starts_at"] == "2026-02-19T10:00:00Z"


def test_rerun_investigation_with_reference_time_now(mock_policy, mock_analysis_json):
    """Test reference_time='now' mode (current state investigation)."""
    with patch("agent.chat.tools.run_investigation") as mock_run:
        mock_investigation = MagicMock()
        mock_investigation.time_window.start_time = datetime(2026, 2, 19, 11, 30, 0, tzinfo=timezone.utc)
        mock_investigation.time_window.end_time = datetime(2026, 2, 19, 12, 0, 0, tzinfo=timezone.utc)
        mock_investigation.time_window.window = "30m"
        mock_run.return_value = mock_investigation

        with patch("agent.chat.tools.investigation_to_json_dict") as mock_json:
            mock_json.return_value = {"analysis": {}}

            with patch("agent.chat.tools.datetime") as mock_datetime:
                # Mock current time
                mock_now = datetime(2026, 2, 19, 12, 0, 0, tzinfo=timezone.utc)
                mock_datetime.now.return_value = mock_now
                mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)

                result = run_tool(
                    tool="rerun.investigation",
                    args={"time_window": "30m", "reference_time": "now"},
                    policy=mock_policy,
                    action_policy=None,
                    analysis_json=mock_analysis_json,
                )

                assert result.ok is True
                alert_passed = mock_run.call_args[1]["alert"]

                # Should use current time, not original alert time
                assert alert_passed["starts_at"] != "2026-02-19T10:00:00Z"
                # Should be close to mock_now
                assert "2026-02-19T12:00:00" in alert_passed["starts_at"]
                # Status should be active for current investigation
                assert alert_passed["status"]["state"] == "active"


def test_rerun_investigation_invalid_reference_time(mock_policy, mock_analysis_json):
    """Test that invalid reference_time values are rejected."""
    result = run_tool(
        tool="rerun.investigation",
        args={"time_window": "1h", "reference_time": "invalid"},
        policy=mock_policy,
        action_policy=None,
        analysis_json=mock_analysis_json,
    )

    assert result.ok is False
    assert result.error == "reference_time_must_be_original_or_now"
