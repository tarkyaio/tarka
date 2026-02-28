from datetime import datetime, timedelta


def test_report_contains_new_sections() -> None:
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

    now = datetime.now()
    start = now - timedelta(hours=1)

    labels = {
        "alertname": "Http5xxRateHigh",
        "severity": "critical",
        "namespace": "prod",
        "pod": "demo-api-7c6d9c8b7d-abc12",
        "container": "app",
        "cluster": "cluster-a",
        "service": "demo-api",
    }
    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp-test",
            labels=labels,
            annotations={
                "summary": "High 5xx rate",
                "description": "5xx responses above threshold",
                "runbook_url": "https://runbooks.example/internal/http5xx",
            },
            starts_at=now.isoformat(),
            state="firing",
        ),
        time_window=TimeWindow(window="1h", start_time=start, end_time=now),
        target=TargetRef(
            target_type="pod",
            namespace="prod",
            pod="demo-api-7c6d9c8b7d-abc12",
            container="app",
            service="demo-api",
            cluster="cluster-a",
            playbook="default",
        ),
        evidence=Evidence(
            k8s=K8sEvidence(
                pod_info={"phase": "Running"},
                pod_conditions=[{"type": "Ready", "status": "True"}],
                pod_events=[{"type": "Normal", "reason": "Started", "message": "Started container app", "count": 1}],
            ),
            metrics=MetricsEvidence(http_5xx={"status": "ok", "series": []}),
            logs=LogsEvidence(
                logs=[{"timestamp": now, "message": "error: upstream timeout", "labels": {}}],
                logs_status="ok",
                logs_backend="victorialogs",
                logs_query='namespace:"prod" AND pod:"demo-api-7c6d9c8b7d-abc12"',
            ),
        ),
        errors=[],
        meta={"source": "test"},
    )

    report = render_report(investigation)

    # Deterministic concise header should exist
    assert "## Verdict" in report
    assert "## Scores" in report
    assert "## Appendix: Evidence" in report


def test_render_report_is_stable_for_fixture() -> None:
    from agent.core.models import AlertInstance, Evidence, Investigation, TargetRef, TimeWindow
    from agent.report import render_report

    now = datetime(2025, 1, 1, 0, 0, 0)
    start = now - timedelta(hours=1)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp-test",
            labels={
                "alertname": "Http5xxRateHigh",
                "severity": "critical",
                "namespace": "prod",
                "pod": "demo-api-7c6d9c8b7d-abc12",
                "container": "app",
                "cluster": "cluster-a",
                "service": "demo-api",
            },
            annotations={},
            starts_at=now.isoformat(),
            state="firing",
        ),
        time_window=TimeWindow(window="1h", start_time=start, end_time=now),
        target=TargetRef(
            target_type="pod",
            namespace="prod",
            pod="demo-api-7c6d9c8b7d-abc12",
            container="app",
            cluster="cluster-a",
            service="demo-api",
            playbook="default",
        ),
        evidence=Evidence(),
        errors=[],
        meta={"source": "test"},
    )
    # Force deterministic header timestamp for golden-ish stability
    md = render_report(investigation, generated_at=now)
    assert "**Generated:** 2025-01-01 00:00:00" in md
