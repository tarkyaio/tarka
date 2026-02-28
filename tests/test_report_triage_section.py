from __future__ import annotations

from datetime import datetime, timezone

from agent.core.models import AlertInstance, Analysis, Investigation, TargetRef, TimeWindow
from agent.report_deterministic import render_deterministic_report


def test_report_includes_triage_section() -> None:
    now = datetime.now(timezone.utc)
    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "TestAlert", "severity": "info"},
            annotations={},
            starts_at=now.isoformat(),
            ends_at=None,
            generator_url=None,
            state="active",
            normalized_state="firing",
            ends_at_kind="expires_at",
        ),
        time_window=TimeWindow(window="15m", start_time=now, end_time=now),
        target=TargetRef(target_type="unknown"),
        analysis=Analysis(),
    )

    md = render_deterministic_report(investigation, generated_at=now)
    assert "## Triage" in md
    assert "### To unblock" in md


def test_report_renders_hypotheses_section_when_present() -> None:
    from datetime import datetime, timezone

    from agent.core.models import AlertInstance, Hypothesis, Investigation, TargetRef, TimeWindow
    from agent.report_deterministic import render_deterministic_report

    now = datetime.now(timezone.utc)
    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "TestAlert", "severity": "info"},
            annotations={},
            starts_at=now.isoformat(),
            state="active",
            normalized_state="firing",
            ends_at_kind="expires_at",
        ),
        time_window=TimeWindow(window="15m", start_time=now, end_time=now),
        target=TargetRef(target_type="pod", namespace="ns1", pod="p1"),
    )
    investigation.analysis.hypotheses = [
        Hypothesis(
            hypothesis_id="test",
            title="Test hypothesis",
            confidence_0_100=77,
            why=["Because reasons."],
            next_tests=["kubectl -n ns1 describe pod p1"],
        )
    ]

    md = render_deterministic_report(investigation, generated_at=now)
    assert "## Likely causes (ranked)" in md
    assert "### Test hypothesis (77/100)" in md
