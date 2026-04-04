"""Unit tests for Slack Block Kit formatter."""

from __future__ import annotations

from datetime import datetime, timezone

from agent.core.models import (
    AlertInstance,
    Analysis,
    DeterministicScores,
    DeterministicVerdict,
    Evidence,
    Investigation,
    TargetRef,
    TimeWindow,
)
from agent.slack.formatter import format_investigation_blocks


def _make_investigation(
    *,
    alertname: str = "KubernetesPodNotHealthy",
    namespace: str = "prod",
    classification: str = "actionable",
    severity: str = "critical",
    one_liner: str = "OOM killed due to memory leak in transaction handler",
    primary_driver: str = "memory_pressure",
    confidence: int = 85,
    next_steps: list[str] | None = None,
) -> Investigation:
    now = datetime.now(timezone.utc)
    return Investigation(
        alert=AlertInstance(
            fingerprint="fp1",
            labels={"alertname": alertname, "namespace": namespace, "severity": severity},
            annotations={},
        ),
        time_window=TimeWindow(window="1h", start_time=now, end_time=now),
        target=TargetRef(namespace=namespace, workload_name="payment-service", environment="prod"),
        evidence=Evidence(),
        analysis=Analysis(
            verdict=DeterministicVerdict(
                classification=classification,
                severity=severity,
                primary_driver=primary_driver,
                one_liner=one_liner,
                next_steps=next_steps or ["Check memory limits", "Review recent commits"],
            ),
            scores=DeterministicScores(
                impact_score=75,
                confidence_score=confidence,
                noise_score=10,
            ),
        ),
    )


def test_format_blocks_returns_fallback_and_blocks() -> None:
    inv = _make_investigation()
    fallback, blocks = format_investigation_blocks(inv)

    assert "KubernetesPodNotHealthy" in fallback
    assert "Actionable" in fallback
    assert len(blocks) > 0


def test_format_blocks_header_contains_alertname() -> None:
    inv = _make_investigation(alertname="CPUThrottlingHigh")
    _, blocks = format_investigation_blocks(inv)

    header = blocks[0]
    assert header["type"] == "header"
    assert "CPUThrottlingHigh" in header["text"]["text"]


def test_format_blocks_includes_verdict_section() -> None:
    inv = _make_investigation(classification="actionable", severity="critical", confidence=90)
    _, blocks = format_investigation_blocks(inv)

    # Find the verdict section
    verdict_blocks = [
        b for b in blocks if b.get("type") == "section" and "Actionable" in b.get("text", {}).get("text", "")
    ]
    assert len(verdict_blocks) == 1
    assert "90/100" in verdict_blocks[0]["text"]["text"]


def test_format_blocks_includes_report_url_button() -> None:
    inv = _make_investigation()
    _, blocks = format_investigation_blocks(inv, report_url="https://tarka.example.com/cases/123")

    action_blocks = [b for b in blocks if b.get("type") == "actions"]
    assert len(action_blocks) == 1
    buttons = action_blocks[0]["elements"]
    assert any(b["text"]["text"] == "View Full Report" for b in buttons)


def test_format_blocks_no_buttons_without_url() -> None:
    inv = _make_investigation()
    _, blocks = format_investigation_blocks(inv)

    action_blocks = [b for b in blocks if b.get("type") == "actions"]
    assert len(action_blocks) == 0


def test_format_blocks_next_steps_limited_to_3() -> None:
    inv = _make_investigation(
        next_steps=["Step 1", "Step 2", "Step 3", "Step 4", "Step 5"],
    )
    _, blocks = format_investigation_blocks(inv)

    steps_blocks = [
        b for b in blocks if b.get("type") == "section" and "Next Steps" in b.get("text", {}).get("text", "")
    ]
    assert len(steps_blocks) == 1
    # Should contain exactly 3 bullet points
    text = steps_blocks[0]["text"]["text"]
    assert text.count("•") == 3


def test_format_blocks_severity_emoji_critical() -> None:
    inv = _make_investigation(severity="critical")
    fallback, _ = format_investigation_blocks(inv)
    assert ":red_circle:" in fallback


def test_format_blocks_severity_emoji_warning() -> None:
    inv = _make_investigation(severity="warning")
    fallback, _ = format_investigation_blocks(inv)
    assert ":large_orange_circle:" in fallback


def test_format_blocks_severity_emoji_info() -> None:
    inv = _make_investigation(severity="info")
    fallback, _ = format_investigation_blocks(inv)
    assert ":large_blue_circle:" in fallback


def test_format_blocks_includes_context_with_case_id() -> None:
    inv = _make_investigation()
    _, blocks = format_investigation_blocks(inv, case_id="abc-123")

    context_blocks = [b for b in blocks if b.get("type") == "context"]
    assert len(context_blocks) == 1
    assert "abc-123" in context_blocks[0]["elements"][0]["text"]


def test_format_blocks_no_context_without_case_id() -> None:
    inv = _make_investigation()
    _, blocks = format_investigation_blocks(inv)

    context_blocks = [b for b in blocks if b.get("type") == "context"]
    assert len(context_blocks) == 0


def test_format_blocks_only_one_button() -> None:
    """Only 'View Full Report' button — no duplicate Chat button."""
    inv = _make_investigation()
    _, blocks = format_investigation_blocks(inv, report_url="https://tarka.example.com/cases/123", case_id="123")

    action_blocks = [b for b in blocks if b.get("type") == "actions"]
    assert len(action_blocks) == 1
    assert len(action_blocks[0]["elements"]) == 1
    assert action_blocks[0]["elements"][0]["action_id"] == "view_report"


def test_classification_label_all_values() -> None:
    from agent.slack.formatter import _classification_label

    assert _classification_label("actionable") == "Actionable"
    assert _classification_label("informational") == "Informational"
    assert _classification_label("noisy") == "Noisy"
    assert _classification_label("artifact") == "Artifact"
    assert _classification_label("unknown_type") == "Unknown"
    assert _classification_label(None) == "Unknown"


def test_severity_emoji_unknown_returns_white_circle() -> None:
    from agent.slack.formatter import _severity_emoji

    assert _severity_emoji("unknown") == ":white_circle:"
    assert _severity_emoji(None) == ":white_circle:"


def test_truncate_short_text_unchanged() -> None:
    from agent.slack.formatter import _truncate

    assert _truncate("short") == "short"


def test_truncate_long_text_adds_ellipsis() -> None:
    from agent.slack.formatter import _truncate

    result = _truncate("x" * 400)
    assert len(result) == 300
    assert result.endswith("...")


def test_truncate_none_returns_dash() -> None:
    from agent.slack.formatter import _truncate

    assert _truncate(None) == "—"
    assert _truncate("") == "—"


def test_format_blocks_no_verdict() -> None:
    """format_investigation_blocks handles missing verdict gracefully."""
    now = datetime.now(timezone.utc)
    inv = Investigation(
        alert=AlertInstance(
            fingerprint="fp2",
            labels={"alertname": "NoVerdictAlert", "severity": "warning"},
            annotations={},
        ),
        time_window=TimeWindow(window="1h", start_time=now, end_time=now),
        target=TargetRef(namespace="staging"),
        evidence=Evidence(),
        analysis=Analysis(verdict=None, scores=None),
    )

    fallback, blocks = format_investigation_blocks(inv)

    assert "NoVerdictAlert" in fallback
    assert len(blocks) > 0
    # No summary or next steps blocks when verdict is None
    summary_blocks = [b for b in blocks if "Summary:" in b.get("text", {}).get("text", "")]
    assert len(summary_blocks) == 0
