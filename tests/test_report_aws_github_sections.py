"""
Tests for AWS and GitHub sections in deterministic reports.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agent.core.models import (
    AlertInstance,
    AwsEvidence,
    Evidence,
    GitHubEvidence,
    Investigation,
    TargetRef,
    TimeWindow,
)
from agent.report_deterministic import render_deterministic_report


def test_report_includes_aws_section_when_evidence_present():
    """Report should render AWS section when AWS evidence is available."""
    now = datetime.now(timezone.utc)
    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "TestAlert", "instance_id": "i-abc123"},
            annotations={},
            starts_at=now.isoformat(),
            state="active",
            normalized_state="firing",
            ends_at_kind="expires_at",
        ),
        time_window=TimeWindow(window="15m", start_time=now, end_time=now),
        target=TargetRef(target_type="pod", namespace="default", pod="test-pod"),
        evidence=Evidence(
            aws=AwsEvidence(
                ec2_instances={
                    "i-abc123": {
                        "state": "running",
                        "system_status": "ok",
                        "instance_status": "ok",
                    }
                },
                ebs_volumes={
                    "vol-xyz789": {
                        "status": "ok",
                        "volume_type": "gp3",
                        "iops": 3000,
                    }
                },
                metadata={"region": "us-east-1"},
            )
        ),
    )

    md = render_deterministic_report(investigation, generated_at=now)
    assert "### AWS" in md
    assert "i-abc123" in md
    assert "vol-xyz789" in md
    assert "us-east-1" in md


def test_report_excludes_aws_section_when_no_evidence():
    """Report should not render AWS section when no AWS evidence is available."""
    now = datetime.now(timezone.utc)
    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "TestAlert"},
            annotations={},
            starts_at=now.isoformat(),
            state="active",
            normalized_state="firing",
            ends_at_kind="expires_at",
        ),
        time_window=TimeWindow(window="15m", start_time=now, end_time=now),
        target=TargetRef(target_type="pod"),
    )

    md = render_deterministic_report(investigation, generated_at=now)
    assert "### AWS" not in md


def test_report_renders_aws_ec2_warnings():
    """Report should show warnings for EC2 instances with issues."""
    now = datetime.now(timezone.utc)
    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "TestAlert"},
            annotations={},
            starts_at=now.isoformat(),
            state="active",
            normalized_state="firing",
            ends_at_kind="expires_at",
        ),
        time_window=TimeWindow(window="15m", start_time=now, end_time=now),
        target=TargetRef(target_type="pod"),
        evidence=Evidence(
            aws=AwsEvidence(
                ec2_instances={
                    "i-failing": {
                        "state": "running",
                        "system_status": "impaired",
                        "instance_status": "ok",
                        "scheduled_events": [
                            {
                                "code": "system-reboot",
                                "not_before": "2026-02-20T10:00:00Z",
                            }
                        ],
                    }
                },
                metadata={"region": "us-west-2"},
            )
        ),
    )

    md = render_deterministic_report(investigation, generated_at=now)
    assert "### AWS" in md
    assert "i-failing" in md
    assert "impaired" in md
    assert "system-reboot" in md


def test_report_renders_aws_ebs_throttling():
    """Report should show EBS volume performance warnings."""
    now = datetime.now(timezone.utc)
    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "TestAlert"},
            annotations={},
            starts_at=now.isoformat(),
            state="active",
            normalized_state="firing",
            ends_at_kind="expires_at",
        ),
        time_window=TimeWindow(window="15m", start_time=now, end_time=now),
        target=TargetRef(target_type="pod"),
        evidence=Evidence(
            aws=AwsEvidence(
                ebs_volumes={
                    "vol-throttled": {
                        "status": "ok",
                        "volume_type": "gp2",
                        "iops": 100,
                        "performance_warnings": ["Volume is being throttled (IOPS limit exceeded)"],
                    }
                },
                metadata={"region": "us-east-1"},
            )
        ),
    )

    md = render_deterministic_report(investigation, generated_at=now)
    assert "### AWS" in md
    assert "vol-throttled" in md
    assert "throttled" in md


def test_report_includes_github_section_when_repo_discovered():
    """Report should render GitHub section when repo is discovered."""
    now = datetime.now(timezone.utc)
    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "TestAlert"},
            annotations={},
            starts_at=now.isoformat(),
            state="active",
            normalized_state="firing",
            ends_at_kind="expires_at",
        ),
        time_window=TimeWindow(window="15m", start_time=now, end_time=now),
        target=TargetRef(target_type="pod", workload_name="my-service"),
        evidence=Evidence(
            github=GitHubEvidence(
                repo="myorg/my-service",
                repo_discovery_method="naming_convention",
                is_third_party=False,
                recent_commits=[
                    {
                        "sha": "abc1234",
                        "author": "alice",
                        "message": "fix: increase connection pool size",
                        "timestamp": "2026-02-18T10:00:00Z",
                    }
                ],
                workflow_runs=[
                    {
                        "id": 12345,
                        "workflow_name": "CI",
                        "status": "completed",
                        "conclusion": "success",
                        "created_at": "2026-02-18T10:05:00Z",
                    }
                ],
            )
        ),
    )

    md = render_deterministic_report(investigation, generated_at=now)
    assert "### GitHub / Changes" in md
    assert "myorg/my-service" in md
    assert "naming_convention" in md
    assert "abc1234" in md
    assert "alice" in md
    assert "increase connection pool size" in md


def test_report_excludes_github_section_when_no_repo():
    """Report should not render GitHub section when no repo is discovered."""
    now = datetime.now(timezone.utc)
    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "TestAlert"},
            annotations={},
            starts_at=now.isoformat(),
            state="active",
            normalized_state="firing",
            ends_at_kind="expires_at",
        ),
        time_window=TimeWindow(window="15m", start_time=now, end_time=now),
        target=TargetRef(target_type="pod"),
    )

    md = render_deterministic_report(investigation, generated_at=now)
    assert "### GitHub" not in md


def test_report_shows_third_party_services():
    """Report should indicate when service is third-party."""
    now = datetime.now(timezone.utc)
    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "TestAlert"},
            annotations={},
            starts_at=now.isoformat(),
            state="active",
            normalized_state="firing",
            ends_at_kind="expires_at",
        ),
        time_window=TimeWindow(window="15m", start_time=now, end_time=now),
        target=TargetRef(target_type="pod", workload_name="coredns"),
        evidence=Evidence(
            github=GitHubEvidence(
                repo="coredns/coredns",
                repo_discovery_method="third_party_catalog",
                is_third_party=True,
            )
        ),
    )

    md = render_deterministic_report(investigation, generated_at=now)
    assert "### GitHub / Changes" in md
    assert "coredns/coredns" in md
    assert "third-party" in md


def test_report_renders_failed_workflow_logs():
    """Report should show failed workflow logs when available."""
    now = datetime.now(timezone.utc)
    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "TestAlert"},
            annotations={},
            starts_at=now.isoformat(),
            state="active",
            normalized_state="firing",
            ends_at_kind="expires_at",
        ),
        time_window=TimeWindow(window="15m", start_time=now, end_time=now),
        target=TargetRef(target_type="pod"),
        evidence=Evidence(
            github=GitHubEvidence(
                repo="myorg/myservice",
                repo_discovery_method="annotation",
                is_third_party=False,
                workflow_runs=[
                    {
                        "id": 12345,
                        "workflow_name": "Deploy",
                        "status": "completed",
                        "conclusion": "failure",
                        "created_at": "2026-02-18T10:00:00Z",
                        "jobs": [
                            {
                                "id": 101,
                                "name": "build-and-push",
                                "status": "completed",
                                "conclusion": "failure",
                            }
                        ],
                    }
                ],
                failed_workflow_logs="Error: buildx failed\nconnection timeout to registry",
            )
        ),
    )

    md = render_deterministic_report(investigation, generated_at=now)
    assert "### GitHub / Changes" in md
    assert "❌" in md  # Failed workflow emoji
    assert "failure" in md
    assert "build-and-push" in md
    assert "buildx failed" in md


def test_report_renders_documentation_availability():
    """Report should show available documentation."""
    now = datetime.now(timezone.utc)
    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "TestAlert"},
            annotations={},
            starts_at=now.isoformat(),
            state="active",
            normalized_state="firing",
            ends_at_kind="expires_at",
        ),
        time_window=TimeWindow(window="15m", start_time=now, end_time=now),
        target=TargetRef(target_type="pod"),
        evidence=Evidence(
            github=GitHubEvidence(
                repo="myorg/myservice",
                repo_discovery_method="annotation",
                is_third_party=False,
                readme="# My Service\n\nThis is the README.",
                docs=[
                    {"path": "docs/runbook.md", "content": "Runbook content..."},
                    {"path": "docs/architecture.md", "content": "Architecture..."},
                ],
            )
        ),
    )

    md = render_deterministic_report(investigation, generated_at=now)
    assert "### GitHub / Changes" in md
    assert "Documentation:" in md
    assert "README.md available" in md
    assert "docs/runbook.md available" in md
    assert "docs/architecture.md available" in md
