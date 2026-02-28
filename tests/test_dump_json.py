from datetime import datetime, timedelta


def test_investigation_to_json_dict_analysis_mode_contains_features_scores_verdict() -> None:
    from agent.core.models import AlertInstance, Investigation, TimeWindow
    from agent.dump import investigation_to_json_dict
    from agent.pipeline.features import compute_features
    from agent.pipeline.scoring import score_investigation

    end = datetime(2025, 1, 1, 0, 0, 0)
    start = end - timedelta(hours=1)
    tw = TimeWindow(window="1h", start_time=start, end_time=end)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "KubePodCrashLooping", "namespace": "ns1", "pod": "p1", "container": "app"},
            annotations={},
        ),
        time_window=tw,
        target={"namespace": "ns1", "pod": "p1", "container": "app", "playbook": "default"},
        evidence={
            "k8s": {
                "pod_info": {
                    "phase": "Running",
                    "container_statuses": [{"name": "app", "state": {"waiting": {"reason": "CrashLoopBackOff"}}}],
                }
            },
            "metrics": {
                "restart_data": {"restart_increase_5m": [{"metric": {"container": "app"}, "values": [[0, "4"]]}]}
            },
        },
    )

    f = compute_features(investigation)
    s, v = score_investigation(investigation, f)
    investigation.analysis.features = f
    investigation.analysis.scores = s
    investigation.analysis.verdict = v

    out = investigation_to_json_dict(investigation, mode="analysis")
    assert out["analysis"]["features"]["family"] == "crashloop"
    assert "impact_score" in out["analysis"]["scores"]
    assert "classification" in out["analysis"]["verdict"]
    # Hypotheses are part of the stable analysis contract (may be empty).
    assert "hypotheses" in out["analysis"]


def test_dump_includes_alert_core_and_source_labels() -> None:
    from datetime import datetime, timedelta

    from agent.core.models import AlertInstance, Investigation, TimeWindow
    from agent.dump import investigation_to_json_dict

    end = datetime(2025, 1, 1, 0, 0, 0)
    start = end - timedelta(hours=1)
    tw = TimeWindow(window="1h", start_time=start, end_time=end)

    # Simulate kube-state-metrics-driven pod alert where job/service/container are scrape metadata.
    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={
                "alertname": "KubernetesPodNotHealthy",
                "severity": "info",
                "namespace": "test",
                "pod": "room-management-api-xxx",
                "job": "kube-state-metrics",
                "service": "victoria-metrics-kube-state-metrics",
                "instance": "10.0.0.1:8080",
                "container": "kube-state-metrics",
                "cluster": "c1",
            },
            annotations={},
        ),
        time_window=tw,
        target={"target_type": "pod", "namespace": "test", "pod": "room-management-api-xxx", "cluster": "c1"},
    )

    out = investigation_to_json_dict(investigation, mode="analysis")
    core = out["alert"]["core_labels"]
    src = out["alert"]["source_labels"]
    labels = out["alert"]["labels"]
    assert core["target_type"] == "pod"
    assert core["namespace"] == "test"
    assert core["pod"] == "room-management-api-xxx"
    # Don't treat scrape container as the affected container
    assert core.get("container") in (None, "")
    assert src["job"] == "kube-state-metrics"
    assert "scrape_container" in src
    # In analysis JSON, keep labels compact (avoid duplication of scrape metadata).
    assert "job" not in labels
    assert "service" not in labels
    assert "instance" not in labels
    assert "endpoint" not in labels
    assert "prometheus" not in labels
    assert "container" not in labels


def test_dump_analysis_mode_includes_evidence_with_parsed_errors() -> None:
    """Test that analysis mode includes evidence section with parsed_errors for RCA context."""
    from datetime import datetime, timedelta

    from agent.core.models import AlertInstance, Evidence, Investigation, LogsEvidence, TimeWindow
    from agent.dump import investigation_to_json_dict

    end = datetime(2025, 1, 1, 0, 0, 0)
    start = end - timedelta(hours=1)
    tw = TimeWindow(window="1h", start_time=start, end_time=end)

    # Create investigation with logs evidence including parsed_errors
    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "KubeJobFailed", "namespace": "ns1", "job_name": "test-job"},
            annotations={},
        ),
        time_window=tw,
        target={"target_type": "pod", "namespace": "ns1", "pod": "test-job-xxx"},
        evidence=Evidence(
            logs=LogsEvidence(
                logs=[{"line": f"log entry {i}"} for i in range(50)],  # 50 log entries
                logs_status="success",
                logs_reason="",
                parsed_errors=[
                    {
                        "pattern": "403 Forbidden",
                        "message": "Access denied to S3 bucket my-bucket",
                        "count": 12,
                        "first_seen": "2025-01-01T00:05:00Z",
                        "last_seen": "2025-01-01T00:45:00Z",
                    },
                    {
                        "pattern": "NoSuchBucket",
                        "message": "The specified bucket does not exist",
                        "count": 3,
                        "first_seen": "2025-01-01T00:10:00Z",
                        "last_seen": "2025-01-01T00:15:00Z",
                    },
                ],
            )
        ),
    )

    out = investigation_to_json_dict(investigation, mode="analysis")

    # Verify evidence section exists
    assert "evidence" in out
    assert "logs" in out["evidence"]

    # Verify logs metadata is included
    logs = out["evidence"]["logs"]
    assert logs["status"] == "success"
    assert logs["reason"] == ""
    assert logs["count"] == 50

    # Verify parsed_errors are included (critical for RCA)
    assert "parsed_errors" in logs
    assert len(logs["parsed_errors"]) == 2
    assert logs["parsed_errors"][0]["pattern"] == "403 Forbidden"
    assert logs["parsed_errors"][0]["count"] == 12
    assert logs["parsed_errors"][1]["pattern"] == "NoSuchBucket"


def test_dump_analysis_mode_handles_missing_logs_evidence() -> None:
    """Test that analysis mode gracefully handles missing logs evidence."""
    from datetime import datetime, timedelta

    from agent.core.models import AlertInstance, Investigation, TimeWindow
    from agent.dump import investigation_to_json_dict

    end = datetime(2025, 1, 1, 0, 0, 0)
    start = end - timedelta(hours=1)
    tw = TimeWindow(window="1h", start_time=start, end_time=end)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "KubePodCrashLooping", "namespace": "ns1", "pod": "p1"},
            annotations={},
        ),
        time_window=tw,
        target={"target_type": "pod", "namespace": "ns1", "pod": "p1"},
        # No evidence section
    )

    out = investigation_to_json_dict(investigation, mode="analysis")

    # Verify evidence section exists with default/empty logs
    assert "evidence" in out
    assert "logs" in out["evidence"]
    logs = out["evidence"]["logs"]
    assert logs["status"] is None
    assert logs["reason"] is None
    assert logs["count"] == 0
    assert logs["parsed_errors"] == []


def test_dump_analysis_mode_handles_logs_without_parsed_errors() -> None:
    """Test that analysis mode handles logs evidence without parsed_errors."""
    from datetime import datetime, timedelta

    from agent.core.models import AlertInstance, Evidence, Investigation, LogsEvidence, TimeWindow
    from agent.dump import investigation_to_json_dict

    end = datetime(2025, 1, 1, 0, 0, 0)
    start = end - timedelta(hours=1)
    tw = TimeWindow(window="1h", start_time=start, end_time=end)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "KubePodCrashLooping", "namespace": "ns1", "pod": "p1"},
            annotations={},
        ),
        time_window=tw,
        target={"target_type": "pod", "namespace": "ns1", "pod": "p1"},
        evidence=Evidence(
            logs=LogsEvidence(
                logs=[],  # Empty logs
                logs_status="empty",
                logs_reason="no_logs_backend",
                parsed_errors=[],  # Empty list
            )
        ),
    )

    out = investigation_to_json_dict(investigation, mode="analysis")

    # Verify evidence section exists with logs metadata
    assert "evidence" in out
    assert "logs" in out["evidence"]
    logs = out["evidence"]["logs"]
    assert logs["status"] == "empty"
    assert logs["reason"] == "no_logs_backend"
    assert logs["count"] == 0
    assert logs["parsed_errors"] == []


def test_dump_analysis_mode_includes_github_evidence_when_repo_discovered() -> None:
    """Test that analysis mode includes github evidence metadata when repo was discovered."""
    from datetime import datetime, timedelta

    from agent.core.models import AlertInstance, Evidence, GitHubEvidence, Investigation, TimeWindow
    from agent.dump import investigation_to_json_dict

    end = datetime(2025, 1, 1, 0, 0, 0)
    start = end - timedelta(hours=1)
    tw = TimeWindow(window="1h", start_time=start, end_time=end)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "KubeJobFailed", "namespace": "ns1", "job_name": "etl-job"},
            annotations={},
        ),
        time_window=tw,
        target={"target_type": "pod", "namespace": "ns1", "pod": "etl-job-xxx"},
        evidence=Evidence(
            github=GitHubEvidence(
                repo="myorg/etl-job",
                repo_discovery_method="service_catalog",
                is_third_party=False,
                recent_commits=[{"sha": "abc123", "message": "fix: update deps"}],
            )
        ),
    )

    out = investigation_to_json_dict(investigation, mode="analysis")

    # Verify github evidence metadata is included
    assert "evidence" in out
    assert "github" in out["evidence"]
    gh = out["evidence"]["github"]
    assert gh["repo"] == "myorg/etl-job"
    assert gh["repo_discovery_method"] == "service_catalog"
    assert gh["is_third_party"] is False
    # Full commit data should NOT be in the lightweight metadata
    assert "recent_commits" not in gh


def test_dump_analysis_mode_github_none_when_no_repo() -> None:
    """Test that evidence.github is None when no repo was discovered."""
    from datetime import datetime, timedelta

    from agent.core.models import AlertInstance, Investigation, TimeWindow
    from agent.dump import investigation_to_json_dict

    end = datetime(2025, 1, 1, 0, 0, 0)
    start = end - timedelta(hours=1)
    tw = TimeWindow(window="1h", start_time=start, end_time=end)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "KubePodCrashLooping", "namespace": "ns1", "pod": "p1"},
            annotations={},
        ),
        time_window=tw,
        target={"target_type": "pod", "namespace": "ns1", "pod": "p1"},
    )

    out = investigation_to_json_dict(investigation, mode="analysis")

    # GitHub should be None when no repo was discovered
    assert out["evidence"]["github"] is None
