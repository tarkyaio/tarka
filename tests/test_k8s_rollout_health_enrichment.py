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
    nxt = rest.find("\n## ", 1)
    if nxt < 0:
        return rest.strip() + "\n"
    return rest[:nxt].strip() + "\n"


def test_report_contains_k8s_rollout_health_enrichment_snapshot() -> None:
    from pathlib import Path

    from agent.core.models import AlertInstance, Evidence, Investigation, K8sEvidence, TargetRef, TimeWindow
    from agent.report import render_report

    now = datetime(2025, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
    start = now - timedelta(minutes=30)
    tw = TimeWindow(window="30m", start_time=start, end_time=now)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={
                "alertname": "KubeDeploymentReplicasMismatch",
                "severity": "warning",
                "namespace": "ns1",
                "deployment": "api",
            },
            annotations={"summary": "deployment replicas mismatch"},
            starts_at=now.isoformat(),
            state="active",
            normalized_state="firing",
            ends_at_kind="expires_at",
        ),
        time_window=tw,
        target=TargetRef(target_type="workload", namespace="ns1", workload_kind="Deployment", workload_name="api"),
        evidence=Evidence(
            k8s=K8sEvidence(
                rollout_status={
                    "kind": "Deployment",
                    "name": "api",
                    "replicas": 5,
                    "updated_replicas": 2,
                    "ready_replicas": 2,
                    "unavailable_replicas": 3,
                    "conditions": [
                        {
                            "type": "Progressing",
                            "status": "False",
                            "reason": "ProgressDeadlineExceeded",
                            "message": 'ReplicaSet "api-abc" has timed out progressing.',
                        }
                    ],
                }
            )
        ),
        meta={"source": "test"},
    )

    md = render_report(investigation, generated_at=now)
    sec = _extract_section(md, "## Enrichment")
    assert sec.startswith("## Enrichment")

    fixture = Path("tests/fixtures/enrichment/k8s_rollout_health.section.md").read_text()
    assert sec.rstrip() == fixture.rstrip()
