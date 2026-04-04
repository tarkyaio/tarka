"""
Slack provider — thin WebClient wrapper with singleton getter/setter for testability.

Follows the same pattern as ``agent/providers/github_provider.py``.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Protocol

logger = logging.getLogger(__name__)


class SlackProvider(Protocol):
    """Protocol for Slack API access."""

    def post_message(
        self,
        *,
        channel: str,
        text: str,
        blocks: Optional[List[Dict[str, Any]]] = None,
        thread_ts: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Post a message to a Slack channel. Returns the Slack API response dict."""
        ...

    def update_message(
        self,
        *,
        channel: str,
        ts: str,
        text: str,
        blocks: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Update an existing Slack message."""
        ...

    def get_user_info(self, *, user_id: str) -> Dict[str, Any]:
        """Get user profile info by Slack user ID."""
        ...

    def set_status(self, *, channel: str, thread_ts: str, status: str) -> None:
        """Set assistant thread status (renders pulsating working state in Slack)."""
        ...

    def clear_status(self, *, channel: str, thread_ts: str) -> None:
        """Clear assistant thread status."""
        ...

    def add_reaction(self, *, channel: str, timestamp: str, name: str) -> None:
        """Add an emoji reaction to a message."""
        ...

    def remove_reaction(self, *, channel: str, timestamp: str, name: str) -> None:
        """Remove an emoji reaction from a message."""
        ...


class DefaultSlackProvider:
    """
    Default Slack provider using ``slack_sdk.WebClient``.

    Requires ``SLACK_BOT_TOKEN`` env var (xoxb-...).
    """

    def __init__(self) -> None:
        from slack_sdk import WebClient  # type: ignore[import-not-found]

        token = os.getenv("SLACK_BOT_TOKEN", "")
        if not token:
            raise ValueError("SLACK_BOT_TOKEN is required for Slack integration")
        self._client = WebClient(token=token)

    def post_message(
        self,
        *,
        channel: str,
        text: str,
        blocks: Optional[List[Dict[str, Any]]] = None,
        thread_ts: Optional[str] = None,
    ) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {"channel": channel, "text": text}
        if blocks:
            kwargs["blocks"] = blocks
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        resp = self._client.chat_postMessage(**kwargs)
        return resp.data  # type: ignore[return-value]

    def update_message(
        self,
        *,
        channel: str,
        ts: str,
        text: str,
        blocks: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {"channel": channel, "ts": ts, "text": text}
        if blocks:
            kwargs["blocks"] = blocks
        resp = self._client.chat_update(**kwargs)
        return resp.data  # type: ignore[return-value]

    def get_user_info(self, *, user_id: str) -> Dict[str, Any]:
        resp = self._client.users_info(user=user_id)
        return resp.data  # type: ignore[return-value]

    def set_status(self, *, channel: str, thread_ts: str, status: str) -> None:
        try:
            self._client.assistant_threads_setStatus(
                channel_id=channel,
                thread_ts=thread_ts,
                status=status,
            )
        except Exception as e:
            logger.debug("Slack set_status failed (best-effort): %s", e)

    def clear_status(self, *, channel: str, thread_ts: str) -> None:
        try:
            self._client.assistant_threads_setStatus(
                channel_id=channel,
                thread_ts=thread_ts,
                status="",
            )
        except Exception as e:
            logger.debug("Slack clear_status failed (best-effort): %s", e)

    def add_reaction(self, *, channel: str, timestamp: str, name: str) -> None:
        try:
            self._client.reactions_add(channel=channel, timestamp=timestamp, name=name)
        except Exception as e:
            logger.debug("Slack add_reaction failed (best-effort): %s", e)

    def remove_reaction(self, *, channel: str, timestamp: str, name: str) -> None:
        try:
            self._client.reactions_remove(channel=channel, timestamp=timestamp, name=name)
        except Exception as e:
            logger.debug("Slack remove_reaction failed (best-effort): %s", e)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_slack_provider: Optional[SlackProvider] = None


def get_slack_provider() -> Optional[SlackProvider]:
    """
    Get Slack provider instance (singleton).

    Returns None if SLACK_BOT_TOKEN is not configured.
    """
    global _slack_provider
    if _slack_provider is None:
        token = os.getenv("SLACK_BOT_TOKEN", "")
        if not token:
            return None
        try:
            _slack_provider = DefaultSlackProvider()
        except Exception as e:
            logger.warning("Failed to initialize Slack provider: %s", e)
            return None
    return _slack_provider


def set_slack_provider(provider: Optional[SlackProvider]) -> None:
    """Set Slack provider instance (for testing)."""
    global _slack_provider
    _slack_provider = provider
