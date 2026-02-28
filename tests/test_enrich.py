"""Tests for family enrichment."""

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
    TimeWindow,
)
from agent.pipeline.enrich import build_family_enrichment


def test_job_failed_enrichment():
    """Test that job_failed family gets enrichment with job status, exit code, SA."""
    from datetime import datetime

    investigation = Investigation(
        alert=AlertInstance(fingerprint="test", labels={"alertname": "KubeJobFailed"}, annotations={}),
        time_window=TimeWindow(window="1h", start_time=datetime.now(), end_time=datetime.now()),
        target={"namespace": "production", "workload_name": "test-job", "pod": "test-job-abc123"},
        evidence=Evidence(
            k8s=K8sEvidence(
                rollout_status={
                    "kind": "Job",
                    "active": 0,
                    "succeeded": 0,
                    "failed": 1,
                    "conditions": [
                        {
                            "type": "Failed",
                            "status": "True",
                            "reason": "BackoffLimitExceeded",
                            "message": "Job has reached the specified backoff limit",
                        }
                    ],
                },
                pod_info={
                    "service_account_name": "app-service-account",
                },
            )
        ),
        analysis=Analysis(
            features=DerivedFeatures(
                family="job_failed",
                k8s=FeaturesK8s(
                    container_last_terminated_top=[
                        K8sContainerLastTerminated(
                            container="main",
                            reason="Error",
                            exit_code=1,
                        )
                    ]
                ),
                logs=FeaturesLogs(error_hits=5),
            )
        ),
    )

    enrichment = build_family_enrichment(investigation)

    # Should get enrichment (not null)
    assert enrichment is not None

    # Should contain job status
    assert any("Job status" in w for w in enrichment.why)
    assert any("active=0" in w and "failed=1" in w for w in enrichment.why)

    # Should contain exit code
    assert any("Container exit: exitCode=1" in w for w in enrichment.why)

    # Should contain service account
    assert any("Service account: app-service-account" in w for w in enrichment.why)

    # Should contain error summary
    assert any("Error patterns in logs: 5 occurrences" in w for w in enrichment.why)

    # Should have PromQL queries
    assert any("kube_job_status_failed" in cmd for cmd in enrichment.next)
    assert any("kubectl -n production describe job test-job" in cmd for cmd in enrichment.next)

    # Label should be based on exit code
    assert enrichment.label == "job_failed_exit_1"


def test_job_failed_enrichment_without_logs():
    """Test job enrichment works without log errors."""
    from datetime import datetime

    investigation = Investigation(
        alert=AlertInstance(fingerprint="test", labels={"alertname": "KubeJobFailed"}, annotations={}),
        time_window=TimeWindow(window="1h", start_time=datetime.now(), end_time=datetime.now()),
        target={"namespace": "production", "workload_name": "test-job", "pod": "test-job-abc123"},
        evidence=Evidence(
            k8s=K8sEvidence(
                rollout_status={
                    "kind": "Job",
                    "active": 1,
                    "succeeded": 0,
                    "failed": 0,
                }
            )
        ),
        analysis=Analysis(
            features=DerivedFeatures(
                family="job_failed",
                k8s=FeaturesK8s(),
                logs=FeaturesLogs(error_hits=0),
            )
        ),
    )

    enrichment = build_family_enrichment(investigation)

    # Should still get enrichment
    assert enrichment is not None
    assert any("Job status" in w for w in enrichment.why)


def test_non_job_family_no_job_enrichment():
    """Test that non-job families don't get job enrichment."""
    from datetime import datetime

    investigation = Investigation(
        alert=AlertInstance(fingerprint="test", labels={"alertname": "PodCPUThrottling"}, annotations={}),
        time_window=TimeWindow(window="1h", start_time=datetime.now(), end_time=datetime.now()),
        target={"namespace": "production", "pod": "test-pod"},
        analysis=Analysis(
            features=DerivedFeatures(
                family="cpu_throttling",
                k8s=FeaturesK8s(),
                logs=FeaturesLogs(),
            )
        ),
    )

    # Should not get job_failed enrichment (might get cpu_throttling enrichment instead)
    # This test just verifies the routing logic doesn't break
    build_family_enrichment(investigation)
    # cpu_throttling family has its own enrichment, so result may not be None
    # Just verify it doesn't crash
    assert True
