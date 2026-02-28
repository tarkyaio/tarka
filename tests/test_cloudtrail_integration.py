"""Unit tests for CloudTrail integration (provider, collector, report, chat tool)."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from agent.collectors.aws_context import (
    _extract_region,
    _extract_resource_ids,
    _group_cloudtrail_events,
    collect_cloudtrail_events,
)
from agent.core.models import AlertInstance, AwsEvidence, Evidence, Investigation, TargetRef

# ============================================================================
# Test CloudTrail Provider Implementation
# ============================================================================


def test_cloudtrail_provider_lookup_events():
    """Test lookup_cloudtrail_events() with mocked boto3 CloudTrail client."""
    from agent.providers.aws_provider import lookup_cloudtrail_events

    # Mock boto3 client
    mock_cloudtrail = MagicMock()
    mock_events = [
        {
            "EventName": "AuthorizeSecurityGroupIngress",
            "EventTime": datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
            "Username": "admin",
            "EventId": "event-123",
            "Resources": [{"ResourceName": "sg-abc123"}],
            "CloudTrailEvent": '{"eventName": "AuthorizeSecurityGroupIngress"}',
        },
        {
            "EventName": "TerminateInstances",
            "EventTime": datetime(2024, 1, 15, 10, 35, 0, tzinfo=timezone.utc),
            "Username": "deploy-bot",
            "EventId": "event-456",
            "Resources": [{"ResourceName": "i-xyz789"}],
            "CloudTrailEvent": '{"eventName": "TerminateInstances"}',
        },
    ]

    mock_cloudtrail.lookup_events.return_value = {"Events": mock_events}

    with patch("agent.providers.aws_provider._get_boto3_client", return_value=mock_cloudtrail):
        start_time = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        end_time = datetime(2024, 1, 15, 11, 0, 0, tzinfo=timezone.utc)

        events = lookup_cloudtrail_events(region="us-east-1", start_time=start_time, end_time=end_time, max_results=50)

        # Verify API called correctly
        mock_cloudtrail.lookup_events.assert_called_once()
        call_args = mock_cloudtrail.lookup_events.call_args[1]
        assert call_args["StartTime"] == start_time
        assert call_args["EndTime"] == end_time
        assert call_args["MaxResults"] == 50

        # Verify events returned
        assert len(events) == 2
        assert events[0]["EventName"] == "AuthorizeSecurityGroupIngress"
        assert events[1]["EventName"] == "TerminateInstances"


def test_cloudtrail_provider_pagination():
    """Test lookup_cloudtrail_events() handles pagination correctly."""
    from agent.providers.aws_provider import lookup_cloudtrail_events

    mock_cloudtrail = MagicMock()

    # First page
    page1_events = [
        {
            "EventName": "RunInstances",
            "EventTime": datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
            "Username": "user1",
            "EventId": "event-1",
            "Resources": [],
            "CloudTrailEvent": "{}",
        }
    ]

    # Second page
    page2_events = [
        {
            "EventName": "StopInstances",
            "EventTime": datetime(2024, 1, 15, 10, 5, 0, tzinfo=timezone.utc),
            "Username": "user2",
            "EventId": "event-2",
            "Resources": [],
            "CloudTrailEvent": "{}",
        }
    ]

    mock_cloudtrail.lookup_events.side_effect = [
        {"Events": page1_events, "NextToken": "token123"},
        {"Events": page2_events},
    ]

    with patch("agent.providers.aws_provider._get_boto3_client", return_value=mock_cloudtrail):
        start_time = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        end_time = datetime(2024, 1, 15, 11, 0, 0, tzinfo=timezone.utc)

        events = lookup_cloudtrail_events(region="us-east-1", start_time=start_time, end_time=end_time, max_results=50)

        # Verify two API calls made (pagination)
        assert mock_cloudtrail.lookup_events.call_count == 2

        # Verify both pages returned
        assert len(events) == 2
        assert events[0]["EventName"] == "RunInstances"
        assert events[1]["EventName"] == "StopInstances"


def test_cloudtrail_provider_error_handling():
    """Test lookup_cloudtrail_events() handles errors gracefully."""
    from agent.providers.aws_provider import lookup_cloudtrail_events

    mock_cloudtrail = MagicMock()
    mock_cloudtrail.lookup_events.side_effect = Exception("API error")

    with patch("agent.providers.aws_provider._get_boto3_client", return_value=mock_cloudtrail):
        start_time = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        end_time = datetime(2024, 1, 15, 11, 0, 0, tzinfo=timezone.utc)

        events = lookup_cloudtrail_events(region="us-east-1", start_time=start_time, end_time=end_time, max_results=50)

        # Verify error returned
        assert isinstance(events, list)
        assert len(events) == 1
        assert "error" in events[0]


# ============================================================================
# Test CloudTrail Collector
# ============================================================================


def test_extract_region_from_alert_labels():
    """Test _extract_region() extracts region from alert labels."""
    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="test",
            labels={"region": "us-west-2"},
            annotations={},
            starts_at="2024-01-15T10:00:00Z",
        ),
        target=TargetRef(),
        evidence=Evidence(),
        time_window={"window": "1h", "start_time": datetime.utcnow(), "end_time": datetime.utcnow()},
    )

    region = _extract_region(investigation)
    assert region == "us-west-2"


def test_extract_region_from_aws_metadata():
    """Test _extract_region() extracts region from AWS evidence metadata."""
    investigation = Investigation(
        alert=AlertInstance(fingerprint="test", labels={}, annotations={}, starts_at="2024-01-15T10:00:00Z"),
        target=TargetRef(),
        evidence=Evidence(aws=AwsEvidence(metadata={"region": "eu-west-1"})),
        time_window={"window": "1h", "start_time": datetime.utcnow(), "end_time": datetime.utcnow()},
    )

    region = _extract_region(investigation)
    assert region == "eu-west-1"


def test_extract_region_defaults():
    """Test _extract_region() defaults to us-east-1."""
    investigation = Investigation(
        alert=AlertInstance(fingerprint="test", labels={}, annotations={}, starts_at="2024-01-15T10:00:00Z"),
        target=TargetRef(),
        evidence=Evidence(),
        time_window={"window": "1h", "start_time": datetime.utcnow(), "end_time": datetime.utcnow()},
    )

    region = _extract_region(investigation)
    assert region == "us-east-1"


def test_extract_resource_ids():
    """Test _extract_resource_ids() extracts resource IDs from AWS evidence."""
    investigation = Investigation(
        alert=AlertInstance(fingerprint="test", labels={}, annotations={}, starts_at="2024-01-15T10:00:00Z"),
        target=TargetRef(),
        evidence=Evidence(
            aws=AwsEvidence(
                ec2_instances={"i-abc123": {"state": "running"}},
                ebs_volumes={"vol-xyz789": {"status": "ok"}},
                rds_instances={"mydb": {"status": "available"}},
            )
        ),
        time_window={"window": "1h", "start_time": datetime.utcnow(), "end_time": datetime.utcnow()},
    )

    resource_ids = _extract_resource_ids(investigation)
    assert resource_ids is not None
    assert "i-abc123" in resource_ids
    assert "vol-xyz789" in resource_ids
    assert "mydb" in resource_ids


def test_extract_resource_ids_empty():
    """Test _extract_resource_ids() returns None when no resources found."""
    investigation = Investigation(
        alert=AlertInstance(fingerprint="test", labels={}, annotations={}, starts_at="2024-01-15T10:00:00Z"),
        target=TargetRef(),
        evidence=Evidence(),
        time_window={"window": "1h", "start_time": datetime.utcnow(), "end_time": datetime.utcnow()},
    )

    resource_ids = _extract_resource_ids(investigation)
    assert resource_ids is None


def test_group_cloudtrail_events():
    """Test _group_cloudtrail_events() groups events by category."""
    events = [
        {"EventName": "AuthorizeSecurityGroupIngress", "EventTime": "2024-01-15T10:00:00Z", "Username": "admin"},
        {"EventName": "TerminateInstances", "EventTime": "2024-01-15T10:05:00Z", "Username": "bot"},
        {"EventName": "ModifyDBInstance", "EventTime": "2024-01-15T10:10:00Z", "Username": "dba"},
        {"EventName": "CreateVolume", "EventTime": "2024-01-15T10:15:00Z", "Username": "ops"},
        {"EventName": "UnknownEvent", "EventTime": "2024-01-15T10:20:00Z", "Username": "unknown"},
    ]

    grouped = _group_cloudtrail_events(events)

    # Verify grouping
    assert "security_group" in grouped
    assert len(grouped["security_group"]) == 1
    assert grouped["security_group"][0]["EventName"] == "AuthorizeSecurityGroupIngress"

    assert "ec2_lifecycle" in grouped
    assert len(grouped["ec2_lifecycle"]) == 1
    assert grouped["ec2_lifecycle"][0]["EventName"] == "TerminateInstances"

    assert "database" in grouped
    assert len(grouped["database"]) == 1
    assert grouped["database"][0]["EventName"] == "ModifyDBInstance"

    assert "storage" in grouped
    assert len(grouped["storage"]) == 1
    assert grouped["storage"][0]["EventName"] == "CreateVolume"

    # UnknownEvent should not appear in any category
    for category, category_events in grouped.items():
        for event in category_events:
            assert event["EventName"] != "UnknownEvent"


def test_collect_cloudtrail_events():
    """Test collect_cloudtrail_events() end-to-end with mocked provider."""
    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="test",
            labels={"region": "us-east-1"},
            annotations={},
            starts_at="2024-01-15T10:00:00Z",
            ends_at="2024-01-15T11:00:00Z",
        ),
        target=TargetRef(),
        evidence=Evidence(aws=AwsEvidence(ec2_instances={"i-abc123": {"state": "running"}})),
        time_window={"window": "1h", "start_time": datetime.utcnow(), "end_time": datetime.utcnow()},
    )

    mock_events = [
        {
            "EventName": "TerminateInstances",
            "EventTime": "2024-01-15T10:30:00Z",
            "Username": "admin",
            "EventId": "event-123",
            "Resources": [],
            "CloudTrailEvent": "{}",
        }
    ]

    mock_provider = MagicMock()
    mock_provider.lookup_cloudtrail_events.return_value = mock_events

    with patch("agent.collectors.aws_context.get_aws_provider", return_value=mock_provider):
        expanded_start = datetime(2024, 1, 15, 9, 30, 0, tzinfo=timezone.utc)
        result = collect_cloudtrail_events(investigation, expanded_start, max_results=50)

        assert result is not None
        assert "events" in result
        assert "grouped" in result
        assert "metadata" in result

        # Verify events
        assert len(result["events"]) == 1
        assert result["events"][0]["EventName"] == "TerminateInstances"

        # Verify grouping
        assert "ec2_lifecycle" in result["grouped"]
        assert len(result["grouped"]["ec2_lifecycle"]) == 1

        # Verify metadata
        assert result["metadata"]["event_count"] == 1
        assert result["metadata"]["region"] == "us-east-1"


# ============================================================================
# Test CloudTrail Report Section
# ============================================================================


def test_cloudtrail_report_section():
    """Test CloudTrail report section rendering."""
    from agent.report_deterministic import render_deterministic_report

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="test",
            labels={"alertname": "TestAlert"},
            annotations={},
            starts_at="2024-01-15T10:00:00Z",
            ends_at="2024-01-15T11:00:00Z",
        ),
        target=TargetRef(target_type="pod", namespace="default", pod="test-pod"),
        evidence=Evidence(
            aws=AwsEvidence(
                cloudtrail_grouped={
                    "security_group": [
                        {
                            "EventName": "AuthorizeSecurityGroupIngress",
                            "EventTime": "2024-01-15T10:30:00Z",
                            "Username": "admin",
                        }
                    ],
                    "ec2_lifecycle": [
                        {"EventName": "TerminateInstances", "EventTime": "2024-01-15T10:35:00Z", "Username": "bot"}
                    ],
                },
                cloudtrail_metadata={"event_count": 2, "time_window": "2024-01-15T09:30:00Z to 2024-01-15T11:00:00Z"},
            )
        ),
        time_window={"window": "1h", "start_time": datetime.utcnow(), "end_time": datetime.utcnow()},
    )

    report = render_deterministic_report(investigation)

    # Verify CloudTrail section appears
    assert "### CloudTrail / Infrastructure Changes" in report
    assert "2 management events" in report

    # Verify categories appear
    assert "Security Group Changes" in report
    assert "EC2 Lifecycle" in report

    # Verify event details appear
    assert "AuthorizeSecurityGroupIngress" in report
    assert "TerminateInstances" in report
    assert "admin" in report
    assert "bot" in report

    # Verify emojis appear
    assert "🔒" in report  # Security-related
    assert "⚙️" in report  # Infrastructure changes


# ============================================================================
# Test CloudTrail Chat Tool
# ============================================================================


def test_cloudtrail_chat_tool_basic():
    """Test aws.cloudtrail_events chat tool basic functionality."""
    from agent.authz.policy import ChatPolicy
    from agent.chat.tools import run_tool

    # Mock investigation with CloudTrail context
    analysis_json = {
        "alert": {"starts_at": "2024-01-15T10:00:00Z", "ends_at": "2024-01-15T11:00:00Z"},
        "evidence": {"aws": {"metadata": {"region": "us-east-1", "ec2_instances": ["i-abc123"]}}},
    }

    policy = ChatPolicy(allow_aws_read=True)

    mock_events = [
        {
            "EventName": "TerminateInstances",
            "EventTime": "2024-01-15T10:30:00Z",
            "Username": "admin",
            "EventId": "event-123",
            "Resources": [],
            "CloudTrailEvent": "{}",
        }
    ]

    mock_provider = MagicMock()
    mock_provider.lookup_cloudtrail_events.return_value = mock_events

    with patch("agent.providers.aws_provider.get_aws_provider", return_value=mock_provider):
        result = run_tool(
            policy=policy,
            action_policy=None,
            tool="aws.cloudtrail_events",
            args={"max_results": 20},
            analysis_json=analysis_json,
        )

        assert result.ok is True
        assert "events" in result.result
        assert "grouped" in result.result
        assert "metadata" in result.result
        assert len(result.result["events"]) == 1


def test_cloudtrail_chat_tool_policy_gate():
    """Test aws.cloudtrail_events respects allow_aws_read policy."""
    from agent.authz.policy import ChatPolicy
    from agent.chat.tools import run_tool

    policy = ChatPolicy(allow_aws_read=False)
    analysis_json = {"alert": {}, "evidence": {"aws": {"metadata": {}}}}

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="aws.cloudtrail_events",
        args={},
        analysis_json=analysis_json,
    )

    assert result.ok is False
    assert result.error == "tool_not_allowed"


def test_cloudtrail_chat_tool_region_allowlist():
    """Test aws.cloudtrail_events respects region allowlist."""
    from agent.authz.policy import ChatPolicy
    from agent.chat.tools import run_tool

    analysis_json = {
        "alert": {"starts_at": "2024-01-15T10:00:00Z"},
        "evidence": {"aws": {"metadata": {"region": "us-west-2"}}},
    }

    policy = ChatPolicy(allow_aws_read=True, aws_region_allowlist=["us-east-1"])

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="aws.cloudtrail_events",
        args={},
        analysis_json=analysis_json,
    )

    assert result.ok is False
    assert "region_not_allowed" in result.error


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
