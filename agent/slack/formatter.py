"""
Investigation → Slack Block Kit message formatter.

Produces structured Slack messages from Investigation objects using Block Kit.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agent.core.models import Investigation


def _severity_emoji(severity: Optional[str]) -> str:
    return {
        "critical": ":red_circle:",
        "warning": ":large_orange_circle:",
        "info": ":large_blue_circle:",
    }.get((severity or "").lower(), ":white_circle:")


def _classification_label(classification: Optional[str]) -> str:
    return {
        "actionable": "Actionable",
        "informational": "Informational",
        "noisy": "Noisy",
        "artifact": "Artifact",
    }.get((classification or "").lower(), "Unknown")


def _truncate(text: Optional[str], max_len: int = 300) -> str:
    if not text:
        return "—"
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def format_investigation_blocks(
    investigation: Investigation,
    *,
    report_url: Optional[str] = None,
    case_id: Optional[str] = None,
) -> tuple[str, list[dict[str, Any]]]:
    """
    Build Slack Block Kit blocks for an investigation notification.

    Returns:
        (fallback_text, blocks) — fallback_text is used for notifications/accessibility.
    """
    labels = investigation.alert.labels if isinstance(investigation.alert.labels, dict) else {}
    alertname = labels.get("alertname", "Unknown Alert")
    namespace = investigation.target.namespace or "—"
    pod = investigation.target.pod
    workload = investigation.target.workload_name
    target_name = workload or pod or "—"
    environment = investigation.target.environment or "—"

    verdict = investigation.analysis.verdict
    scores = investigation.analysis.scores

    classification = verdict.classification if verdict else None
    severity = verdict.severity if verdict else labels.get("severity")
    primary_driver = verdict.primary_driver if verdict else None
    one_liner = verdict.one_liner if verdict else None
    next_steps = verdict.next_steps if verdict else []
    confidence = scores.confidence_score if scores else None

    emoji = _severity_emoji(severity)
    cls_label = _classification_label(classification)

    # Fallback text (shown in notifications, search, accessibility)
    fallback = f"{emoji} {alertname} — {cls_label}: {_truncate(one_liner, 100)}"

    blocks: List[Dict[str, Any]] = []

    # Header
    blocks.append(
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{alertname}", "emoji": True},
        }
    )

    # Target context
    target_parts = [f"*Target:* `{target_name}`"]
    if namespace != "—":
        target_parts.append(f"*Namespace:* `{namespace}`")
    if environment != "—":
        target_parts.append(f"*Environment:* {environment}")
    blocks.append(
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "  |  ".join(target_parts)},
        }
    )

    # Verdict line
    verdict_parts = [f"{emoji} *{cls_label}*"]
    if severity:
        verdict_parts.append(f"Severity: *{severity.capitalize()}*")
    if confidence is not None:
        verdict_parts.append(f"Confidence: *{confidence}/100*")
    blocks.append(
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "  |  ".join(verdict_parts)},
        }
    )

    # Summary (one-liner)
    if one_liner:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Summary:* {_truncate(one_liner)}"},
            }
        )

    # Primary driver
    if primary_driver:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Primary Driver:* {_truncate(primary_driver)}"},
            }
        )

    # Next steps (top 3)
    if next_steps:
        steps_text = "\n".join(f"• {_truncate(s, 200)}" for s in next_steps[:3])
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Next Steps:*\n{steps_text}"},
            }
        )

    # Divider before actions
    blocks.append({"type": "divider"})

    # Action buttons
    actions_elements: List[Dict[str, Any]] = []
    if report_url:
        actions_elements.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "View Full Report", "emoji": True},
                "url": report_url,
                "action_id": "view_report",
            }
        )

    if actions_elements:
        blocks.append({"type": "actions", "elements": actions_elements})

    # Context footer
    context_parts = [f"Case: {case_id}" if case_id else ""]
    context_parts = [p for p in context_parts if p]
    if context_parts:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": " | ".join(context_parts)}],
            }
        )

    return fallback, blocks
