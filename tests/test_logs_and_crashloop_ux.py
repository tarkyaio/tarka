from __future__ import annotations

from datetime import datetime, timedelta, timezone


def test_logs_snippet_prioritizes_errors_over_startup_banners() -> None:
    from agent.core.models import (
        AlertInstance,
        Evidence,
        Investigation,
        K8sEvidence,
        LogsEvidence,
        MetricsEvidence,
        TargetRef,
        TimeWindow,
    )
    from agent.report import render_report

    end = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    start = end - timedelta(hours=1)

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
            starts_at=end.isoformat(),
            normalized_state="firing",
        ),
        time_window=TimeWindow(window="1h", start_time=start, end_time=end),
        target=TargetRef(target_type="pod", namespace="ns1", pod="p1", container="app", playbook="default"),
        evidence=Evidence(
            k8s=K8sEvidence(
                pod_info={"phase": "Running", "container_statuses": [{"name": "app", "restart_count": 10}]},
                pod_conditions=[{"type": "Ready", "status": "False"}],
            ),
            metrics=MetricsEvidence(
                restart_data={"restart_increase_5m": [{"metric": {"container": "app"}, "values": [[0, "4"]]}]}
            ),
            logs=LogsEvidence(
                logs_status="ok",
                logs_backend="victorialogs",
                logs_query='namespace:"ns1" AND pod:"p1" AND container:"app"',
                logs=[
                    {
                        "timestamp": end - timedelta(minutes=1),
                        "message": "  .   ____          _            __ _ _\n"
                        " \\\\/  ___)| |_)| | | | | || (_| |  ) ) ) )\n"
                        " =========|_|==============|___/=/_/_/_/\n",
                        "labels": {},
                    },
                    {
                        "timestamp": end - timedelta(minutes=2),
                        "message": "ERROR failed to connect to upstream\njava.lang.RuntimeException: boom",
                        "labels": {},
                    },
                ],
            ),
        ),
    )

    md = render_report(investigation, generated_at=end)
    # We should show the actionable error line(s), not the ASCII banner.
    assert "ERROR failed to connect to upstream" in md
    assert "____" not in md


def test_logs_snippet_does_not_prefer_exception_handler_config_over_error() -> None:
    from agent.core.models import AlertInstance, Evidence, Investigation, LogsEvidence, TargetRef, TimeWindow
    from agent.report import render_report

    end = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    start = end - timedelta(hours=1)

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
            starts_at=end.isoformat(),
            normalized_state="firing",
        ),
        time_window=TimeWindow(window="1h", start_time=start, end_time=end),
        target=TargetRef(target_type="pod", namespace="ns1", pod="p1", container="app", playbook="default"),
        evidence=Evidence(
            logs=LogsEvidence(
                logs_status="ok",
                logs_backend="victorialogs",
                logs_query='namespace:"ns1" AND pod:"p1" AND container:"app"',
                logs=[
                    {
                        "timestamp": end - timedelta(minutes=3),
                        "message": "default.production.exception.handler = class org.apache.kafka.streams.errors.DefaultProductionExceptionHandler",
                        "labels": {},
                    },
                    {
                        "timestamp": end - timedelta(minutes=2),
                        "message": "ERROR something actually failed",
                        "labels": {},
                    },
                ],
            ),
        ),
    )

    md = render_report(investigation, generated_at=end)
    assert "ERROR something actually failed" in md
    assert "exception.handler" not in md


def test_crashloop_verdict_cites_concrete_evidence() -> None:
    from agent.core.models import AlertInstance, Investigation, TimeWindow
    from agent.pipeline.features import compute_features
    from agent.pipeline.scoring import score_investigation

    end = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
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
                "pod_info": {"phase": "Running", "container_statuses": [{"name": "app", "restart_count": 10}]},
                "pod_conditions": [{"type": "Ready", "status": "False"}],
                "pod_events": [
                    {
                        "type": "Warning",
                        "reason": "Unhealthy",
                        "message": "Readiness probe failed: HTTP probe failed with statuscode: 503",
                        "count": 3,
                    },
                    {
                        "type": "Warning",
                        "reason": "Unhealthy",
                        "message": "Liveness probe failed: HTTP probe failed with statuscode: 503",
                        "count": 2,
                    },
                ],
            },
            "metrics": {
                "restart_data": {"restart_increase_5m": [{"metric": {"container": "app"}, "values": [[0, "6"]]}]}
            },
        },
    )

    f = compute_features(investigation)
    scores, verdict = score_investigation(investigation, f)
    assert f.family == "crashloop"
    assert scores.impact_score >= 60
    assert "restart_rate_5m_max=" in verdict.one_liner
    assert "probe_failures=" in verdict.one_liner
