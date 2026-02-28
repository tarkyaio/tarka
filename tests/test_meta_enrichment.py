from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _extract_section(md: str, header: str) -> str:
    start = md.find(header)
    if start < 0:
        return ""
    rest = md[start:]
    nxt = rest.find("\n## ", 1)
    if nxt < 0:
        return rest.strip() + "\n"
    return rest[:nxt].strip() + "\n"


def test_report_contains_meta_enrichment_snapshot() -> None:
    from pathlib import Path

    from agent.core.models import AlertInstance, Investigation, TargetRef, TimeWindow
    from agent.report import render_report

    now = datetime(2025, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
    start = now - timedelta(minutes=30)
    tw = TimeWindow(window="30m", start_time=start, end_time=now)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "InfoInhibitor", "severity": "info"},
            annotations={"summary": "inhibitor"},
            starts_at=now.isoformat(),
            state="active",
            normalized_state="firing",
            ends_at_kind="expires_at",
        ),
        time_window=tw,
        target=TargetRef(target_type="unknown"),
    )

    md = render_report(investigation, generated_at=now)
    sec = _extract_section(md, "## Enrichment")
    assert sec.startswith("## Enrichment")
    fixture = Path("tests/fixtures/enrichment/meta.section.md").read_text()
    assert sec.rstrip() == fixture.rstrip()
