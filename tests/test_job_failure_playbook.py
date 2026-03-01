"""Tests for Job failure playbook."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from agent.core.models import AlertInstance, Evidence, Investigation, TargetRef, TimeWindow
from agent.core.targets import should_ignore_pod_label_for_jobs
from agent.playbooks.job_failure import investigate_job_failure_playbook


@pytest.fixture
def mock_k8s_provider():
    """Mock K8s provider for testing."""
    with patch("agent.collectors.job_failure.get_k8s_provider") as mock:
        provider = MagicMock()
        # Default: no pods found (simulates TTL deletion)
        provider.list_pods.return_value = []
        mock.return_value = provider
        yield provider


@pytest.fixture
def mock_workload_rollout_status():
    """Mock workload rollout status."""
    with patch("agent.collectors.job_failure.get_workload_rollout_status") as mock:
        mock.return_value = {
            "kind": "Job",
            "name": "test-job",
            "active": 0,
            "succeeded": 0,
            "failed": 1,
            "start_time": "2026-02-18T10:00:00Z",
            "completion_time": "2026-02-18T10:05:00Z",
        }
        yield mock


@pytest.fixture
def mock_get_events():
    """Mock K8s events."""
    with patch("agent.collectors.job_failure.get_events") as mock:
        mock.return_value = [
            {
                "type": "Warning",
                "reason": "BackoffLimitExceeded",
                "message": "Job has reached the specified backoff limit",
                "count": 1,
                "last_timestamp": "2026-02-18T10:05:00Z",
            }
        ]
        yield mock


@pytest.fixture
def mock_gather_pod_context():
    """Mock pod context gathering."""
    with patch("agent.collectors.job_failure.gather_pod_context") as mock:
        mock.return_value = {
            "pod_info": {
                "name": "test-job-abc123",
                "namespace": "default",
                "phase": "Failed",
            },
            "pod_conditions": [{"type": "Ready", "status": "False", "reason": "PodFailed"}],
            "pod_events": [
                {
                    "type": "Warning",
                    "reason": "Failed",
                    "message": "Error: exit code 1",
                }
            ],
            "errors": [],
        }
        yield mock


@pytest.fixture
def mock_fetch_recent_logs():
    """Mock logs fetching."""
    with patch("agent.collectors.job_failure.fetch_recent_logs") as mock:
        mock.return_value = {
            "entries": [{"timestamp": "2026-02-18T10:04:30Z", "message": "Error: Database connection failed"}],
            "status": "ok",
            "reason": None,
            "backend": "victorialogs",
            "query_used": '{namespace="default",pod=~"test-job-.*"}',
        }
        yield mock


@patch("agent.collectors.job_failure.apply_historical_fallback")
def test_job_failure_blocked_no_pods(mock_historical, mock_k8s_provider, mock_workload_rollout_status):
    """Test Job failure playbook when pods are TTL-deleted and historical fallback finds nothing."""
    # Historical fallback runs but finds no logs → blocked mode
    mock_historical.return_value = None

    # Create investigation with Job target
    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp123",
            labels={"alertname": "KubeJobFailed", "namespace": "default", "job_name": "test-job"},
            annotations={},
            starts_at=datetime(2026, 2, 18, 10, 5, 0, tzinfo=timezone.utc).isoformat(),
        ),
        target=TargetRef(
            target_type="workload",
            namespace="default",
            workload_kind="Job",
            workload_name="test-job",
        ),
        time_window=TimeWindow(
            window="1h",
            start_time=datetime(2026, 2, 18, 9, 5, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 2, 18, 10, 5, 0, tzinfo=timezone.utc),
        ),
        evidence=Evidence(),
    )

    # Mock: no pods found (TTL-deleted)
    mock_k8s_provider.list_pods.return_value = []

    # Run playbook
    investigate_job_failure_playbook(investigation)

    # Verify historical fallback was attempted
    mock_historical.assert_called_once_with(investigation, pod_404=True)

    # Verify blocked mode set (fallback returned no logs)
    assert investigation.meta.get("blocked_mode") == "job_pods_not_found"
    assert investigation.target.playbook == "job_failure"

    # Verify error message added
    assert any("No pods found for Job test-job" in err for err in investigation.errors)

    # Verify K8s provider was called with correct label selector
    mock_k8s_provider.list_pods.assert_called_once_with(namespace="default", label_selector="job-name=test-job")


def test_job_failure_with_pods_found(
    mock_k8s_provider,
    mock_workload_rollout_status,
    mock_get_events,
    mock_gather_pod_context,
    mock_fetch_recent_logs,
):
    """Test Job failure playbook when pods are found."""
    # Create investigation with Job target
    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp123",
            labels={"alertname": "KubeJobFailed", "namespace": "default", "job_name": "test-job"},
            annotations={},
            starts_at=datetime(2026, 2, 18, 10, 5, 0, tzinfo=timezone.utc).isoformat(),
        ),
        target=TargetRef(
            target_type="workload",
            namespace="default",
            workload_kind="Job",
            workload_name="test-job",
        ),
        time_window=TimeWindow(
            window="1h",
            start_time=datetime(2026, 2, 18, 9, 5, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 2, 18, 10, 5, 0, tzinfo=timezone.utc),
        ),
        evidence=Evidence(),
    )

    # Mock: pods found
    mock_k8s_provider.list_pods.return_value = [
        {
            "metadata": {
                "name": "test-job-abc123",
                "namespace": "default",
                "creationTimestamp": "2026-02-18T10:00:05Z",
                "labels": {"job-name": "test-job"},
            },
            "status": {"phase": "Failed"},
        }
    ]

    # Run playbook
    investigate_job_failure_playbook(investigation)

    # Verify NOT in blocked mode
    assert investigation.meta.get("blocked_mode") is None

    # Verify target pod populated
    assert investigation.target.pod == "test-job-abc123"
    assert investigation.target.target_type == "pod"
    assert investigation.meta.get("job_pods_found") == 1
    assert investigation.meta.get("job_pod_investigated") == "test-job-abc123"

    # Verify K8s context gathered
    assert investigation.evidence.k8s.pod_info is not None
    assert investigation.evidence.k8s.pod_info["name"] == "test-job-abc123"

    # Verify logs collected
    assert investigation.evidence.logs.logs_status == "ok"
    assert len(investigation.evidence.logs.logs) == 1


def test_job_failure_time_window_adjustment(
    mock_k8s_provider,
    mock_workload_rollout_status,
):
    """Test that time window is adjusted to Job start time."""
    # Create investigation with default time window
    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp123",
            labels={"alertname": "KubeJobFailed", "namespace": "default", "job_name": "test-job"},
            annotations={},
            starts_at=datetime(2026, 2, 18, 10, 5, 0, tzinfo=timezone.utc).isoformat(),
        ),
        target=TargetRef(
            target_type="workload",
            namespace="default",
            workload_kind="Job",
            workload_name="test-job",
        ),
        time_window=TimeWindow(
            window="1h",
            start_time=datetime(2026, 2, 18, 9, 5, 0, tzinfo=timezone.utc),  # Default: now - 1h
            end_time=datetime(2026, 2, 18, 10, 5, 0, tzinfo=timezone.utc),
        ),
        evidence=Evidence(),
    )

    # Mock: no pods (will enter blocked mode, but time adjustment should still happen)
    mock_k8s_provider.list_pods.return_value = []

    # Run playbook
    investigate_job_failure_playbook(investigation)

    # Verify time window adjusted to Job start time
    expected_start = datetime(2026, 2, 18, 10, 0, 0, tzinfo=timezone.utc)
    assert investigation.time_window.start_time == expected_start
    assert investigation.meta.get("time_window_adjusted") == "job_start_time"
    assert "job_lifetime_" in investigation.time_window.window


def test_job_failure_multiple_pods_selects_newest(
    mock_k8s_provider,
    mock_workload_rollout_status,
    mock_get_events,
    mock_gather_pod_context,
    mock_fetch_recent_logs,
):
    """Test that most recent pod is selected when Job has multiple pods."""
    # Create investigation
    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp123",
            labels={"alertname": "KubeJobFailed", "namespace": "default", "job_name": "test-job"},
            annotations={},
            starts_at=datetime(2026, 2, 18, 10, 5, 0, tzinfo=timezone.utc).isoformat(),
        ),
        target=TargetRef(
            target_type="workload",
            namespace="default",
            workload_kind="Job",
            workload_name="test-job",
        ),
        time_window=TimeWindow(
            window="1h",
            start_time=datetime(2026, 2, 18, 9, 5, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 2, 18, 10, 5, 0, tzinfo=timezone.utc),
        ),
        evidence=Evidence(),
    )

    # Mock: multiple pods (Job retried)
    mock_k8s_provider.list_pods.return_value = [
        {
            "metadata": {
                "name": "test-job-first",
                "namespace": "default",
                "creationTimestamp": "2026-02-18T10:00:05Z",
                "labels": {"job-name": "test-job"},
            },
            "status": {"phase": "Failed"},
        },
        {
            "metadata": {
                "name": "test-job-second",
                "namespace": "default",
                "creationTimestamp": "2026-02-18T10:02:10Z",  # Newer
                "labels": {"job-name": "test-job"},
            },
            "status": {"phase": "Failed"},
        },
    ]

    # Run playbook
    investigate_job_failure_playbook(investigation)

    # Verify most recent pod selected
    assert investigation.target.pod == "test-job-second"
    assert investigation.meta.get("job_pods_found") == 2


def test_job_identity_extraction_from_alert_labels(
    mock_k8s_provider,
    mock_workload_rollout_status,
    mock_get_events,
    mock_gather_pod_context,
    mock_fetch_recent_logs,
):
    """Test that Job identity is extracted from alert labels (job_name).

    This is the critical fix: KubeJobFailed alerts have job_name label with the actual Job,
    but the pod label points to the kube-state-metrics scrape pod (incorrect).
    """
    # Create investigation WITHOUT workload identity (simulating pipeline state before collector runs)
    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp123",
            labels={
                "alertname": "KubeJobFailed",
                "namespace": "production",
                "job_name": "batch-etl-job-57438-0-lmwj3",  # ← Correct Job name
                "pod": "prometheus-kube-state-metrics-99bf89fcf-z5rmg",  # ← WRONG (scrape pod)
                "job": "kube-state-metrics",  # ← Prometheus scrape job
            },
            annotations={},
            starts_at=datetime(2026, 2, 18, 10, 5, 0, tzinfo=timezone.utc).isoformat(),
        ),
        target=TargetRef(
            target_type="pod",
            namespace="production",
            # workload_kind and workload_name NOT set yet (will be extracted by collector)
        ),
        time_window=TimeWindow(
            window="1h",
            start_time=datetime(2026, 2, 18, 9, 5, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 2, 18, 10, 5, 0, tzinfo=timezone.utc),
        ),
        evidence=Evidence(),
    )

    # Mock: job pods found
    mock_k8s_provider.list_pods.return_value = [
        {
            "metadata": {
                "name": "batch-etl-job-57438-0-lmwj3-7g5jl",
                "namespace": "production",
                "creationTimestamp": "2026-02-18T10:00:05Z",
                "labels": {"job-name": "batch-etl-job-57438-0-lmwj3"},
            },
            "status": {"phase": "Failed"},
        }
    ]

    # Run playbook
    investigate_job_failure_playbook(investigation)

    # Verify Job identity was extracted from alert labels
    assert investigation.target.workload_kind == "Job"
    assert investigation.target.workload_name == "batch-etl-job-57438-0-lmwj3"

    # Verify correct pod was found (NOT the kube-state-metrics pod)
    assert investigation.target.pod == "batch-etl-job-57438-0-lmwj3-7g5jl"
    assert investigation.target.pod != "prometheus-kube-state-metrics-99bf89fcf-z5rmg"

    # Verify evidence was collected successfully
    assert investigation.evidence.k8s.pod_info is not None
    assert investigation.evidence.logs.logs_status == "ok"

    # Verify NOT in blocked mode
    assert investigation.meta.get("blocked_mode") is None
    assert not any("missing Job identity" in err for err in investigation.errors)


def test_job_identity_extraction_missing_job_name_label(mock_k8s_provider):
    """Test error handling when job_name label is missing from alert."""
    # Create investigation without job_name label
    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp123",
            labels={
                "alertname": "KubeJobFailed",
                "namespace": "default",
                # job_name is MISSING
            },
            annotations={},
            starts_at=datetime(2026, 2, 18, 10, 5, 0, tzinfo=timezone.utc).isoformat(),
        ),
        target=TargetRef(
            target_type="pod",
            namespace="default",
        ),
        time_window=TimeWindow(
            window="1h",
            start_time=datetime(2026, 2, 18, 9, 5, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 2, 18, 10, 5, 0, tzinfo=timezone.utc),
        ),
        evidence=Evidence(),
    )

    # Run playbook
    investigate_job_failure_playbook(investigation)

    # Verify error message is helpful
    assert any("missing Job identity" in err for err in investigation.errors)
    # Verify error message includes available alert labels for debugging
    assert any("Alert labels:" in err for err in investigation.errors)


def test_should_ignore_pod_label_for_kubejobfailed():
    """Test helper function that detects Job alerts with incorrect pod label."""
    # KubeJobFailed with job_name → should ignore pod label
    labels = {
        "alertname": "KubeJobFailed",
        "job_name": "my-job",
        "pod": "prometheus-kube-state-metrics-xyz",
    }
    assert should_ignore_pod_label_for_jobs(labels) is True

    # JobFailed (alternative naming) with job_name → should ignore pod label
    labels = {
        "alertname": "JobFailed",
        "job_name": "my-job",
        "pod": "prometheus-kube-state-metrics-xyz",
    }
    assert should_ignore_pod_label_for_jobs(labels) is True

    # KubeJobFailed without job_name → should NOT ignore pod label
    labels = {
        "alertname": "KubeJobFailed",
        "pod": "some-pod",
    }
    assert should_ignore_pod_label_for_jobs(labels) is False

    # Different alert type → should NOT ignore pod label
    labels = {
        "alertname": "PodNotHealthy",
        "job_name": "my-job",
        "pod": "my-app-pod",
    }
    assert should_ignore_pod_label_for_jobs(labels) is False

    # Case insensitivity
    labels = {
        "alertname": "KUBEJOBFAILED",
        "job_name": "my-job",
        "pod": "prometheus-kube-state-metrics-xyz",
    }
    assert should_ignore_pod_label_for_jobs(labels) is True


def test_job_failure_clears_scrape_target_fields(mock_k8s_provider, mock_workload_rollout_status, mock_get_events):
    """
    Test that service/job/instance fields are cleared for Job alerts.

    KubeJobFailed alerts have service="kube-state-metrics", job="kube-state-metrics", etc.
    which refer to the scrape target (monitoring stack), not the actual failing Job.
    These should be cleared to avoid confusion in UI "Affected Components".

    This test verifies the fix for: prometheus-kube-state-metrics appearing in affected components.
    """
    # Create investigation with scrape target fields set (as pipeline does from alert labels)
    investigation = Investigation(
        alert=AlertInstance(
            labels={
                "alertname": "KubeJobFailed",
                "namespace": "default",
                "job_name": "my-data-job",
                "job": "kube-state-metrics",  # Scrape target, not the actual Job
                "service": "prometheus-kube-state-metrics",  # Scrape target
                "instance": "10.0.1.5:8080",  # Scrape endpoint
                "pod": "prometheus-kube-state-metrics-abc123",  # Wrong pod
            },
            annotations={},
        ),
        target=TargetRef(
            namespace="default",
            service="prometheus-kube-state-metrics",  # Set by pipeline from alert labels
            job="kube-state-metrics",  # Set by pipeline
            instance="10.0.1.5:8080",  # Set by pipeline
        ),
        time_window=TimeWindow(
            window="1h",
            start_time=datetime(2026, 2, 18, 10, 0, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 2, 18, 11, 0, 0, tzinfo=timezone.utc),
        ),
        evidence=Evidence(),
        meta={},
    )

    # Run collector (via playbook)
    investigate_job_failure_playbook(investigation)

    # Verify scrape target fields were CLEARED
    assert investigation.target.service is None, "service should be cleared (was scrape target)"
    assert investigation.target.job is None, "job should be cleared (was scrape target)"
    assert investigation.target.instance is None, "instance should be cleared (was scrape endpoint)"

    # Verify Job identity fields were SET correctly
    assert investigation.target.workload_kind == "Job", "workload_kind should be Job"
    assert investigation.target.workload_name == "my-data-job", "workload_name should be actual Job name"
    assert investigation.target.target_type == "pod", "Jobs are pod-scoped"

    # Verify namespace preserved
    assert investigation.target.namespace == "default"


def test_job_failure_report_content_quality(
    mock_k8s_provider, mock_workload_rollout_status, mock_get_events, mock_gather_pod_context
):
    """
    Verify KubeJobFailed report has enrichment, actions, and job-specific evidence.

    This test verifies the improvements from the content quality plan:
    1. Enrichment section populated (job status, exit code, SA)
    2. Hypotheses have proposed_actions (diagnose, fix)
    3. Job-specific metrics in features (exit code, SA, attempts)
    """
    # This is a simplified test that verifies the enrichment works with populated data
    # A full integration test would run the entire pipeline
    from agent.core.models import (
        AlertInstance,
        Analysis,
        DerivedFeatures,
        Evidence,
        FeaturesK8s,
        FeaturesLogs,
        Investigation,
        K8sContainerLastTerminated,
        K8sEvidence,
        LogsEvidence,
        TimeWindow,
    )
    from agent.pipeline.enrich import build_family_enrichment
    from agent.pipeline.job_metrics import compute_job_metrics

    investigation = Investigation(
        alert=AlertInstance(fingerprint="test", labels={"alertname": "KubeJobFailed"}, annotations={}),
        time_window=TimeWindow(window="1h", start_time=datetime.now(timezone.utc), end_time=datetime.now(timezone.utc)),
        target={"namespace": "production", "workload_name": "test-job", "pod": "test-job-abc123"},
        evidence=Evidence(
            k8s=K8sEvidence(
                rollout_status={
                    "kind": "Job",
                    "active": 0,
                    "succeeded": 0,
                    "failed": 1,
                },
                pod_info={"service_account_name": "app-service-account"},
            ),
            logs=LogsEvidence(logs=[{"content": "ERROR: S3 access denied"}]),
        ),
        analysis=Analysis(
            features=DerivedFeatures(
                family="job_failed",
                k8s=FeaturesK8s(
                    container_last_terminated_top=[
                        K8sContainerLastTerminated(container="main", reason="Error", exit_code=1)
                    ]
                ),
                logs=FeaturesLogs(error_hits=5),
            )
        ),
    )

    # Compute job metrics
    compute_job_metrics(investigation)

    # Build enrichment
    enrichment = build_family_enrichment(investigation)

    # 1. Enrichment should be populated
    assert enrichment is not None
    assert len(enrichment.why) > 0
    assert any("Job status" in w for w in enrichment.why)
    assert any("Service account: app-service-account" in w for w in enrichment.why)

    # 2. Next steps should exist
    assert len(enrichment.next) > 0

    # 3. Job-specific metrics should be populated
    assert hasattr(investigation.analysis.features, "job_metrics")
    assert investigation.analysis.features.job_metrics.get("service_account") == "app-service-account"
    assert investigation.analysis.features.job_metrics.get("exit_code") == 1
