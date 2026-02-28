from datetime import datetime, timedelta


def test_cpu_throttling_scores_actionable() -> None:
    from agent.core.models import AlertInstance, Investigation, TimeWindow
    from agent.pipeline.features import compute_features
    from agent.pipeline.scoring import score_investigation

    end = datetime(2025, 1, 1, 0, 0, 0)
    start = end - timedelta(hours=1)
    tw = TimeWindow(window="1h", start_time=start, end_time=end)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={
                "alertname": "CPUThrottlingHigh",
                "severity": "warning",
                "namespace": "ns1",
                "pod": "p1",
                "container": "app",
            },
            annotations={},
        ),
        time_window=tw,
        target={"namespace": "ns1", "pod": "p1", "container": "app", "playbook": "cpu_throttling"},
        evidence={
            "metrics": {
                "throttling_data": {
                    "throttling_percentage": [{"metric": {"container": "app"}, "values": [[0, "30"], [1, "35"]]}]
                },
                "cpu_metrics": {
                    "cpu_usage": [{"metric": {"container": "app"}, "values": [[0, "0.14"], [1, "0.15"]]}],
                    "cpu_limits": [{"metric": {"container": "app"}, "values": [[0, "0.15"]]}],
                },
            }
        },
    )

    f = compute_features(investigation)
    scores, verdict = score_investigation(investigation, f)
    assert f.family == "cpu_throttling"
    # Near-limit case: impact should be high.
    assert scores.impact_score >= 60
    # Metrics-first family: missing logs must NOT reduce confidence.
    assert "MISSING_LOGS" not in scores.reason_codes
    assert scores.confidence_score >= 60
    assert verdict.primary_driver == "cpu_throttling"


def test_crashloop_scores_actionable() -> None:
    from agent.core.models import AlertInstance, Investigation, TimeWindow
    from agent.pipeline.features import compute_features
    from agent.pipeline.scoring import score_investigation

    end = datetime(2025, 1, 1, 0, 0, 0)
    start = end - timedelta(hours=1)
    tw = TimeWindow(window="1h", start_time=start, end_time=end)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={
                "alertname": "KubePodCrashLooping",
                "severity": "warning",
                "namespace": "ns1",
                "pod": "p1",
                "container": "app",
            },
            annotations={},
        ),
        time_window=tw,
        target={"namespace": "ns1", "pod": "p1", "container": "app", "playbook": "default"},
        evidence={
            "k8s": {
                "pod_info": {
                    "phase": "Running",
                    "container_statuses": [
                        {"name": "app", "restart_count": 10, "state": {"waiting": {"reason": "CrashLoopBackOff"}}}
                    ],
                },
                "pod_conditions": [{"type": "Ready", "status": "False"}],
            },
            "metrics": {
                "restart_data": {"restart_increase_5m": [{"metric": {"container": "app"}, "values": [[0, "4"]]}]}
            },
        },
    )
    f = compute_features(investigation)
    scores, verdict = score_investigation(investigation, f)
    assert f.family == "crashloop"
    assert scores.impact_score >= 60
    assert verdict.classification in ("actionable", "informational")
