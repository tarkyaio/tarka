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


def test_http_5xx_enrichment_snapshot() -> None:
    from agent.core.models import (
        AlertInstance,
        Analysis,
        ChangeCorrelation,
        ChangeTimeline,
        Evidence,
        Investigation,
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
            labels={"alertname": "Http5xxRateHigh", "namespace": "ns1", "pod": "p1", "severity": "critical"},
            annotations={},
            starts_at=now.isoformat(),
            state="active",
            normalized_state="firing",
        ),
        time_window=TimeWindow(window="30m", start_time=start, end_time=now),
        target=TargetRef(target_type="pod", namespace="ns1", pod="p1", playbook="http_5xx"),
        evidence=Evidence(
            metrics=MetricsEvidence(
                http_5xx={
                    "query_used": 'sum(rate(http_requests_total{status=~"5.."}[5m]))',
                    "series": [{"metric": {}, "values": [[0, "0.5"], [1, "1.0"]]}],
                }
            )
        ),
        analysis=Analysis(
            change=ChangeCorrelation(
                has_recent_change=True,
                last_change_time=now.isoformat(),
                timeline=ChangeTimeline(source="kubernetes", workload={"kind": "Deployment", "name": "api"}, events=[]),
            )
        ),
    )

    md = render_report(investigation, generated_at=now)
    sec = _extract_section(md, "## Enrichment")
    fixture = Path("tests/fixtures/enrichment/http_5xx.section.md").read_text()
    assert sec.rstrip() == fixture.rstrip()
