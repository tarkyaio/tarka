"""
Slack Bolt app — async Socket Mode handler for inbound chat.

Handles:
- ``app_mention`` events: @tarka in channels
- ``message.im`` events: DMs to Tarka

Bridges Slack threads to the existing Tarka chat runtime (``run_chat_stream``
for case-scoped, ``run_global_chat_stream`` for global).
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Case ID regex: UUID v4 pattern
_CASE_ID_RE = re.compile(r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b", re.IGNORECASE)


def _strip_mention(text: str) -> str:
    """Strip ``<@U...>`` mention prefix from message text."""
    return re.sub(r"<@[A-Z0-9]+>\s*", "", text).strip()


def _extract_case_id(text: str) -> Optional[str]:
    """Extract a UUID-shaped case ID from message text."""
    m = _CASE_ID_RE.search(text)
    return m.group(1) if m else None


def _is_slack_configured() -> bool:
    """Check if both Socket Mode tokens are set."""
    return bool(os.getenv("SLACK_BOT_TOKEN")) and bool(os.getenv("SLACK_APP_TOKEN"))


async def _stream_to_slack(stream) -> str:
    """
    Consume a ChatStreamEvent stream and return the final reply text.

    Status updates and thread management are handled by the caller via
    ``assistant.threads.setStatus`` — this function only collects tokens.
    """
    reply_parts: list[str] = []

    async for event in stream:
        if event.event_type == "token":
            reply_parts.append(event.content)
        elif event.event_type == "error":
            if not reply_parts:
                reply_parts.append(f"Sorry, I hit an error: {event.content}")

    reply = "".join(reply_parts).strip()
    if not reply:
        reply = "I processed the request but didn't generate a response. Try rephrasing?"

    # Truncate for Slack
    if len(reply) > 3000:
        reply = reply[:2950] + "\n\n_...response truncated. View the full report in the Tarka console._"

    return reply


async def _handle_message(
    event: Dict[str, Any],
    say,
    client,
) -> None:
    """
    Core handler for both app_mention and DM events.

    Flow:
    1. Strip @tarka prefix from text
    2. Call assistant.threads.setStatus → renders pulsating working state in Slack
    3. Look up thread mapping (slack channel+thread_ts → tarka thread + case)
    4. Run existing chat runtime, collect reply
    5. Post reply in thread
    6. Clear status → Slack renders "1 reply" natively
    """
    from agent.authz.policy import load_action_policy, load_chat_policy
    from agent.slack.provider import get_slack_provider

    channel = event.get("channel", "")
    text = _strip_mention(event.get("text", ""))
    # status_ts: the ts to anchor setStatus to — thread root if in a thread, else message ts
    status_ts = event.get("thread_ts") or event.get("ts", "")

    if not text:
        return

    provider = get_slack_provider()

    # Immediately render working state — fires before any processing
    if provider:
        provider.set_status(channel=channel, thread_ts=status_ts, status="Gathering information...")

    try:
        # Determine scope: case-scoped or global
        case_id = None
        analysis_json: Optional[Dict[str, Any]] = None

        # 1. Check thread bridge for existing case mapping
        try:
            from agent.slack.thread_bridge import lookup_thread

            mapping = lookup_thread(slack_channel=channel, slack_thread_ts=status_ts)
            if mapping and mapping.case_id:
                case_id = mapping.case_id
        except Exception as e:
            logger.debug("Thread bridge lookup failed: %s", e)

        # 2. Check for explicit case ID in message
        if not case_id:
            case_id = _extract_case_id(text)

        # Load policies
        policy = load_chat_policy()
        if not policy.enabled:
            await say(text="Chat is currently disabled.", thread_ts=status_ts)
            return

        if case_id:
            # Case-scoped chat
            try:
                analysis_json = _load_analysis_json(case_id)
            except Exception:
                analysis_json = None

            if analysis_json:
                from agent.chat.runtime_streaming import run_chat_stream

                action_policy = load_action_policy()
                stream = run_chat_stream(
                    policy=policy,
                    action_policy=action_policy,
                    analysis_json=analysis_json,
                    user_message=text,
                    history=[],
                    case_id=case_id,
                )
            else:
                # Case not found — fall back to global
                from agent.chat.global_runtime_streaming import run_global_chat_stream

                stream = run_global_chat_stream(
                    policy=policy,
                    user_message=text,
                    history=[],
                )
        else:
            # Global chat
            from agent.chat.global_runtime_streaming import run_global_chat_stream

            stream = run_global_chat_stream(
                policy=policy,
                user_message=text,
                history=[],
            )

        reply = await _stream_to_slack(stream)
        await say(text=reply, thread_ts=status_ts)

    except Exception as e:
        logger.exception("Slack chat handler error: %s", e)
        try:
            await say(text=f"Sorry, something went wrong: {str(e)[:200]}", thread_ts=status_ts)
        except Exception:
            pass
    finally:
        # Always clear status — Slack renders "1 reply" once status is gone
        if provider:
            provider.clear_status(channel=channel, thread_ts=status_ts)


def _load_analysis_json(case_id: str) -> Optional[Dict[str, Any]]:
    """Load the latest analysis_json for a case from Postgres."""
    from agent.memory.config import build_postgres_dsn, load_memory_config

    cfg = load_memory_config()
    dsn = build_postgres_dsn(cfg)
    if not dsn:
        return None

    import psycopg  # type: ignore[import-not-found]

    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            """
            SELECT analysis_json
            FROM investigation_runs
            WHERE case_id::text = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (case_id,),
        ).fetchone()
        if not row or not row[0]:
            return None
        analysis_json = row[0]
        if not isinstance(analysis_json, dict):
            try:
                analysis_json = json.loads(str(analysis_json))
            except Exception:
                return None
        return analysis_json


def create_bolt_app():
    """
    Create and configure the Slack Bolt AsyncApp.

    Returns None if Slack is not configured.
    """
    if not _is_slack_configured():
        logger.info("Slack Bolt app not started: SLACK_BOT_TOKEN or SLACK_APP_TOKEN not set")
        return None

    try:
        from slack_bolt.async_app import AsyncApp  # type: ignore[import-not-found]

        app = AsyncApp(
            token=os.getenv("SLACK_BOT_TOKEN"),
            signing_secret=os.getenv("SLACK_SIGNING_SECRET", ""),
        )

        @app.event("assistant_thread_started")
        async def handle_assistant_thread_started(body):
            pass  # Assistant panel not used; handler required to suppress Bolt warning

        @app.event("assistant_thread_context_changed")
        async def handle_assistant_thread_context_changed(body):
            pass  # Assistant panel not used; handler required to suppress Bolt warning

        @app.event("app_mention")
        async def handle_mention(event, say, client):
            await _handle_message(event, say, client)

        @app.event("message")
        async def handle_dm(event, say, client):
            # Only handle DMs (channel_type == "im") to avoid processing all channel messages
            if event.get("channel_type") == "im":
                # Skip bot's own messages
                if event.get("bot_id"):
                    return
                await _handle_message(event, say, client)

        logger.info("Slack Bolt app created successfully")
        return app

    except ImportError:
        logger.warning("slack-bolt not installed — Slack chat disabled")
        return None
    except Exception as e:
        logger.warning("Failed to create Slack Bolt app: %s", e)
        return None


async def start_socket_mode() -> None:
    """
    Start Slack Socket Mode in the background.

    This connects to Slack via WebSocket — no public ingress needed.
    Should be called as a background asyncio task from the webhook server startup.
    """
    bolt_app = create_bolt_app()
    if bolt_app is None:
        return

    try:
        from slack_bolt.adapter.socket_mode.async_handler import (  # type: ignore[import-not-found]
            AsyncSocketModeHandler,
        )

        handler = AsyncSocketModeHandler(bolt_app, os.getenv("SLACK_APP_TOKEN", ""))
        logger.info("Starting Slack Socket Mode...")
        await handler.start_async()
    except ImportError:
        logger.warning("slack-bolt[async] not installed — Socket Mode disabled")
    except Exception as e:
        logger.error("Slack Socket Mode failed: %s", e)
