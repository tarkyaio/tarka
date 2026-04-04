"""
Slack notification dispatcher — best-effort, never raises.

Channel routing priority:
1. Alert label ``slack_channel`` (teams control their own routing via Alertmanager config)
2. ``SLACK_DEFAULT_CHANNEL`` env fallback
3. No channel resolved → skip silently (log warning)

Notification filtering:
- Only notify on ``actionable`` and ``informational`` verdicts.
- Skip ``noisy`` and ``artifact`` classifications.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from agent.core.models import Investigation
from agent.slack.formatter import format_investigation_blocks
from agent.slack.provider import get_slack_provider

logger = logging.getLogger(__name__)

# Classifications that trigger Slack notifications.
_NOTIFY_CLASSIFICATIONS = {"actionable", "informational"}


def _resolve_channel(investigation: Investigation) -> Optional[str]:
    """
    Resolve the target Slack channel for an investigation.

    Priority:
    1. ``slack_channel`` alert label
    2. ``SLACK_DEFAULT_CHANNEL`` env var
    """
    labels = investigation.alert.labels if isinstance(investigation.alert.labels, dict) else {}

    # 1. Alert label override
    channel = (labels.get("slack_channel") or "").strip()
    if channel:
        # Ensure channel starts with # for readability (Slack API accepts both forms)
        return channel

    # 2. Env fallback
    default = os.getenv("SLACK_DEFAULT_CHANNEL", "").strip()
    if default:
        return default

    return None


def _should_notify(investigation: Investigation) -> bool:
    """Return True if this investigation warrants a Slack notification."""
    verdict = investigation.analysis.verdict
    if verdict is None:
        return False
    classification = (verdict.classification or "").lower()
    return classification in _NOTIFY_CLASSIFICATIONS


def notify_investigation_complete(
    *,
    investigation: Investigation,
    report_url: Optional[str] = None,
    case_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Optional[str]:
    """
    Post a Slack notification for a completed investigation.

    Returns the message ``ts`` (timestamp) if posted, or None if skipped/failed.
    This function is best-effort and **never raises**.
    """
    try:
        # Check filtering
        if not _should_notify(investigation):
            classification = (
                investigation.analysis.verdict.classification if investigation.analysis.verdict else "unknown"
            )
            logger.debug("Slack notification skipped: classification=%s", classification)
            return None

        # Resolve channel
        channel = _resolve_channel(investigation)
        if not channel:
            logger.debug("Slack notification skipped: no channel configured")
            return None

        # Get provider (returns None if not configured)
        provider = get_slack_provider()
        if provider is None:
            logger.debug("Slack notification skipped: provider not configured")
            return None

        # Format message
        fallback_text, blocks = format_investigation_blocks(
            investigation,
            report_url=report_url,
            case_id=case_id,
        )

        # Post
        resp = provider.post_message(
            channel=channel,
            text=fallback_text,
            blocks=blocks,
        )

        ts = resp.get("ts")
        posted_channel = resp.get("channel", channel)
        logger.info(
            "Slack notification sent: channel=%s ts=%s case_id=%s",
            posted_channel,
            ts,
            case_id,
        )

        # Best-effort: register the thread for Phase 2 chat bridging
        if ts and case_id:
            try:
                from agent.slack.thread_bridge import register_notification_thread

                register_notification_thread(
                    slack_channel=posted_channel,
                    slack_thread_ts=ts,
                    case_id=case_id,
                )
            except Exception:
                pass  # Phase 2 bridge is optional

        return ts

    except Exception as e:
        logger.warning("Slack notification failed (non-fatal): %s", str(e))
        return None
