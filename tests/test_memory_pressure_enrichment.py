from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path


def _extract_section(md: str, header: str) -> str:
    start = md.find(header)
    if start < 0:
        return ""
    rest = md[start:]
    nxt = rest.find("\n## ", 1)
    if nxt < 0:
        return rest.strip() + "\n"
    return rest[:nxt].strip() + "\n"


def test_memory_pressure_enrichment_snapshot() -> None:
    from agent.core.models import (
        AlertInstance,
        Evidence,
        Investigation,
        K8sEvidence,
        MetricsEvidence,
        TargetRef,
        TimeWindow,
    )
    from agent.report import render_report

    now = datetime(2025, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
    start = now - timedelta(minutes=30)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "MemoryPressure", "namespace": "ns1", "pod": "p1", "container": "app"},
            annotations={},
            starts_at=now.isoformat(),
            state="active",
            normalized_state="firing",
        ),
        time_window=TimeWindow(window="30m", start_time=start, end_time=now),
        target=TargetRef(target_type="pod", namespace="ns1", pod="p1", container="app", playbook="memory_pressure"),
        evidence=Evidence(
            k8s=K8sEvidence(
                pod_info={"phase": "Running", "status_reason": "Pressure"}, pod_conditions=[], pod_events=[]
            ),
            metrics=MetricsEvidence(
                memory_metrics={
                    "memory_usage_bytes": [{"metric": {"container": "app"}, "values": [[0, "900"]]}],
                    "memory_limits_bytes": [{"metric": {"container": "app"}, "values": [[0, "1000"]]}],
                },
                restart_data={"restart_increase_5m": [{"metric": {"container": "app"}, "values": [[0, "0"]]}]},
            ),
        ),
    )

    md = render_report(investigation, generated_at=now)
    sec = _extract_section(md, "## Enrichment")
    fixture = Path("tests/fixtures/enrichment/memory_pressure.section.md").read_text()
    assert sec.rstrip() == fixture.rstrip()
