from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _extract_section(md: str, header: str) -> str:
    """
    Extract a markdown section starting at `header` (e.g. '## Enrichment') up to the next '## ' header.
    """
    start = md.find(header)
    if start < 0:
        return ""
    rest = md[start:]
    # Find next top-level section after the header line.
    nxt = rest.find("\n## ", 1)
    if nxt < 0:
        return rest.strip() + "\n"
    return rest[:nxt].strip() + "\n"


def test_pod_not_healthy_enrichment_label_image_pull() -> None:
    from agent.core.models import (
        AlertInstance,
        Evidence,
        Investigation,
        K8sEvidence,
        LogsEvidence,
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
            labels={"alertname": "KubernetesPodNotHealthy", "namespace": "ns1", "pod": "p1"},
            annotations={},
            starts_at=now.isoformat(),
            state="active",
        ),
        time_window=tw,
        target=TargetRef(target_type="pod", namespace="ns1", pod="p1", playbook="pod_not_healthy"),
        evidence=Evidence(
            k8s=K8sEvidence(
                pod_info={
                    "phase": "Pending",
                    "status_reason": "Unschedulable",
                    "container_statuses": [
                        {"name": "app", "state": {"waiting": {"reason": "ImagePullBackOff", "message": "pull failed"}}},
                    ],
                },
                pod_conditions=[{"type": "Ready", "status": "False"}],
                pod_events=[
                    {
                        "type": "Warning",
                        "reason": "Failed",
                        "message": "Failed to pull image",
                        "count": 1,
                        "last_timestamp": now.isoformat(),
                    }
                ],
            ),
            logs=LogsEvidence(
                logs=[{"timestamp": now, "message": "no logs", "labels": {}}],
                logs_status="ok",
                logs_backend="victorialogs",
                logs_query='namespace:"ns1" AND pod:"p1"',
            ),
        ),
    )
    investigation.analysis.features = compute_features(investigation)

    e = build_family_enrichment(investigation)
    assert e is not None
    assert e.label == "suspected_image_pull_backoff"


def test_pod_not_healthy_enrichment_label_evicted_pressure() -> None:
    from agent.core.models import AlertInstance, Evidence, Investigation, K8sEvidence, TargetRef, TimeWindow
    from agent.pipeline.enrich import build_family_enrichment
    from agent.pipeline.features import compute_features

    now = datetime(2025, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
    start = now - timedelta(minutes=30)
    tw = TimeWindow(window="30m", start_time=start, end_time=now)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "KubernetesPodNotHealthy", "namespace": "ns1", "pod": "p1"},
            annotations={},
            starts_at=now.isoformat(),
            state="active",
        ),
        time_window=tw,
        target=TargetRef(target_type="pod", namespace="ns1", pod="p1", playbook="pod_not_healthy"),
        evidence=Evidence(
            k8s=K8sEvidence(
                pod_info={
                    "phase": "Failed",
                    "status_reason": "Evicted",
                    "status_message": "Pod was rejected: The node had condition: [DiskPressure].",
                    "container_statuses": [],
                },
                pod_conditions=[{"type": "Ready", "status": "False"}],
                pod_events=[],
            ),
        ),
    )
    investigation.analysis.features = compute_features(investigation)
    e = build_family_enrichment(investigation)
    assert e is not None
    assert e.label == "suspected_scheduling_or_node_pressure"


def test_pod_not_healthy_enrichment_label_failed_scheduling() -> None:
    from agent.core.models import AlertInstance, Evidence, Investigation, K8sEvidence, TargetRef, TimeWindow
    from agent.pipeline.enrich import build_family_enrichment
    from agent.pipeline.features import compute_features

    now = datetime(2025, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
    start = now - timedelta(minutes=30)
    tw = TimeWindow(window="30m", start_time=start, end_time=now)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "KubernetesPodNotHealthy", "namespace": "ns1", "pod": "p1"},
            annotations={},
            starts_at=now.isoformat(),
            state="active",
        ),
        time_window=tw,
        target=TargetRef(target_type="pod", namespace="ns1", pod="p1", playbook="pod_not_healthy"),
        evidence=Evidence(
            k8s=K8sEvidence(
                pod_info={
                    "phase": "Pending",
                    "status_reason": "Unschedulable",
                    "status_message": "0/3 nodes are available: Insufficient cpu.",
                    "container_statuses": [],
                },
                pod_conditions=[{"type": "Ready", "status": "False"}],
                pod_events=[
                    {
                        "type": "Warning",
                        "reason": "FailedScheduling",
                        "message": "0/3 nodes are available: Insufficient cpu.",
                        "count": 12,
                        "last_timestamp": now.isoformat(),
                    }
                ],
            ),
        ),
    )
    investigation.analysis.features = compute_features(investigation)
    e = build_family_enrichment(investigation)
    assert e is not None
    assert e.label == "suspected_scheduling_or_node_pressure"


def test_report_contains_enrichment_section_snapshot() -> None:
    from pathlib import Path

    from agent.core.models import (
        AlertInstance,
        Evidence,
        Investigation,
        K8sEvidence,
        LogsEvidence,
        TargetRef,
        TimeWindow,
    )
    from agent.report import render_report

    now = datetime(2025, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
    start = now - timedelta(minutes=30)
    tw = TimeWindow(window="30m", start_time=start, end_time=now)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "KubernetesPodNotHealthy", "severity": "warning", "namespace": "ns1", "pod": "p1"},
            annotations={"summary": "pod unhealthy"},
            starts_at=now.isoformat(),
            state="active",
        ),
        time_window=tw,
        target=TargetRef(target_type="pod", namespace="ns1", pod="p1", playbook="pod_not_healthy"),
        evidence=Evidence(
            k8s=K8sEvidence(
                pod_info={
                    "phase": "Pending",
                    "status_reason": "Unschedulable",
                    "status_message": "0/3 nodes are available: Insufficient cpu.",
                    "container_statuses": [
                        {"name": "app", "state": {"waiting": {"reason": "ImagePullBackOff", "message": "pull failed"}}},
                    ],
                },
                pod_conditions=[{"type": "Ready", "status": "False", "reason": "ContainersNotReady"}],
                pod_events=[
                    {
                        "type": "Warning",
                        "reason": "FailedScheduling",
                        "message": "0/3 nodes are available: Insufficient cpu.",
                        "count": 12,
                        "last_timestamp": now.isoformat(),
                    }
                ],
            ),
            logs=LogsEvidence(
                logs=[{"timestamp": now, "message": "hello", "labels": {}}],
                logs_status="ok",
                logs_backend="victorialogs",
                logs_query='namespace:"ns1" AND pod:"p1"',
            ),
        ),
        meta={"source": "test"},
    )

    md = render_report(investigation, generated_at=now)
    sec = _extract_section(md, "## Enrichment")
    assert sec.startswith("## Enrichment")

    fixture = Path("tests/fixtures/enrichment/pod_not_healthy_imagepull.section.md").read_text()
    # Snapshot files may end with extra trailing newlines depending on editor behavior.
    # Ignore trailing whitespace/newlines but keep exact section body stable.
    assert sec.rstrip() == fixture.rstrip()
