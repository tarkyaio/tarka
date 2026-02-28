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


def test_cpu_throttling_enrichment_snapshot() -> None:
    from agent.core.models import AlertInstance, Evidence, Investigation, MetricsEvidence, TargetRef, TimeWindow
    from agent.report import render_report

    now = datetime(2025, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
    start = now - timedelta(minutes=30)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "CPUThrottlingHigh", "namespace": "ns1", "pod": "p1"},
            annotations={},
            starts_at=now.isoformat(),
            state="active",
            normalized_state="firing",
        ),
        time_window=TimeWindow(window="30m", start_time=start, end_time=now),
        target=TargetRef(target_type="pod", namespace="ns1", pod="p1", playbook="cpu_throttling"),
        evidence=Evidence(
            metrics=MetricsEvidence(
                throttling_data={
                    "throttling_percentage": [{"metric": {"container": "app"}, "values": [[0, "30"], [1, "30"]]}]
                },
                cpu_metrics={
                    "cpu_usage": [{"metric": {"container": "app"}, "values": [[0, "0.8"], [1, "0.8"]]}],
                    "cpu_limits": [{"metric": {"container": "app"}, "values": [[0, "1.0"]]}],
                },
            )
        ),
    )

    md = render_report(investigation, generated_at=now)
    sec = _extract_section(md, "## Enrichment")
    fixture = Path("tests/fixtures/enrichment/cpu_throttling.section.md").read_text()
    assert sec.rstrip() == fixture.rstrip()
