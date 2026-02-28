from __future__ import annotations

from datetime import datetime, timedelta, timezone


def test_oom_killed_enrichment_label_limit_too_low() -> None:
    from agent.core.models import (
        AlertInstance,
        Evidence,
        Investigation,
        K8sEvidence,
        MetricsEvidence,
        TargetRef,
        TimeWindow,
    )
    from agent.pipeline.enrich import build_family_enrichment
    from agent.pipeline.features import compute_features

    now = datetime(2025, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
    start = now - timedelta(minutes=30)
    tw = TimeWindow(window="30m", start_time=start, end_time=now)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "KubernetesContainerOomKiller", "namespace": "ns1", "pod": "p1", "container": "app"},
            annotations={},
            starts_at=now.isoformat(),
            state="active",
        ),
        time_window=tw,
        target=TargetRef(target_type="pod", namespace="ns1", pod="p1", container="app", playbook="oom_killer"),
        evidence=Evidence(
            k8s=K8sEvidence(
                pod_info={
                    "phase": "Running",
                    "container_statuses": [
                        {"name": "app", "last_state": {"terminated": {"reason": "OOMKilled", "exit_code": 137}}},
                    ],
                },
                pod_conditions=[{"type": "Ready", "status": "True"}],
                pod_events=[],
            ),
            metrics=MetricsEvidence(
                memory_metrics={
                    # These shapes get normalized by compute_features; we only need the values to land.
                    "memory_usage_bytes": [{"metric": {"container": "app"}, "values": [[0, "900"]]}],
                    "memory_limits_bytes": [{"metric": {"container": "app"}, "values": [[0, "1000"]]}],
                }
            ),
        ),
    )
    investigation.analysis.features = compute_features(investigation)
    e = build_family_enrichment(investigation)
    assert e is not None
    assert e.label == "suspected_oom_limit_too_low"
