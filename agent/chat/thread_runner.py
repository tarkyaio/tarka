"""
Shared chat streaming core — used by both the web UI endpoint and the Slack handler.

Handles the full lifecycle of a single chat turn:
  1. Resolve or create a Tarka thread (global or case-scoped)
  2. Persist the user message
  3. Load conversation history
  4. Load analysis_json for case threads
  5. Dispatch to the appropriate streaming runtime
  6. Persist the assistant reply + tool events

Yields ``ThreadRunnerEvent`` objects.  The special ``thread_ready`` event is
emitted once at the very beginning so callers can discover the thread ID (e.g.
Slack needs it to register in the thread bridge); all subsequent events mirror
the ChatStreamEvent / GlobalChatStreamEvent shapes from the runtimes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ThreadRunnerEvent:
    """Unified event type for thread_runner consumers."""

    # "thread_ready" is emitted once at start with thread_id/case_id in metadata.
    # All other event_type values pass through from the underlying runtime.
    event_type: str
    content: str = ""
    tool: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


def _load_analysis_json(
    *,
    case_id: str,
    run_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Load analysis_json from investigation_runs.  Returns None if unavailable."""
    from agent.memory.config import build_postgres_dsn, load_memory_config

    cfg = load_memory_config()
    dsn = build_postgres_dsn(cfg)
    if not dsn:
        return None

    import psycopg  # type: ignore[import-not-found]

    with psycopg.connect(dsn) as conn:
        if run_id:
            row = conn.execute(
                """
                SELECT case_id::text, analysis_json
                FROM investigation_runs
                WHERE run_id::text = %s
                """,
                (run_id,),
            ).fetchone()
            if not row or str(row[0] or "") != case_id:
                return None
            raw = row[1]
        else:
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
            raw = row[0] if row else None

    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(str(raw))
    except Exception:
        return None


async def run_thread_message(
    *,
    user_key: str,
    thread_id: Optional[str],
    case_id: Optional[str] = None,
    user_message: str,
    run_id: Optional[str] = None,
) -> AsyncGenerator[ThreadRunnerEvent, None]:
    """
    Run a single chat turn and stream events.

    Args:
        user_key:     Identity of the caller (e.g. ``"slack:U123"`` or a web session key).
        thread_id:    Existing Tarka thread ID, or ``None`` to create one.
        case_id:      Case UUID if this is a case-scoped conversation.
        user_message: The user's message text.
        run_id:       Optional investigation run ID to pin context (web UI only).

    Yields:
        ThreadRunnerEvent — starting with a ``thread_ready`` event, then
        all events from the underlying runtime (thinking, tool_start, token, …).
    """
    from agent.authz.policy import load_action_policy, load_chat_policy
    from agent.chat.types import ChatMessage as ChatMsg
    from agent.memory.chat import (
        append_message,
        get_or_create_case_thread,
        get_or_create_global_thread,
        get_thread,
        insert_tool_events,
        list_messages,
    )

    logger.debug(
        "thread_runner.start user_key=%s thread_id=%s case_id=%s",
        user_key,
        thread_id,
        case_id,
    )

    # --- 1. Resolve or create thread ---
    thr = None
    if thread_id:
        ok, _, thr = get_thread(user_key=user_key, thread_id=thread_id)

    if thr is None:
        if case_id:
            ok, msg, thr = get_or_create_case_thread(user_key=user_key, case_id=case_id)
        else:
            ok, msg, thr = get_or_create_global_thread(user_key=user_key)
        if thr is None:
            logger.error("thread_runner.thread_create_failed user_key=%s error=%s", user_key, msg)
            yield ThreadRunnerEvent(event_type="error", content="Could not create chat thread.")
            return

    thread_id = thr.thread_id
    if not case_id and thr.case_id:
        case_id = thr.case_id

    logger.info(
        "thread_runner.thread_ready thread_id=%s kind=%s case_id=%s",
        thr.thread_id,
        thr.kind,
        thr.case_id,
    )
    yield ThreadRunnerEvent(
        event_type="thread_ready",
        metadata={"thread_id": thr.thread_id, "case_id": thr.case_id, "kind": thr.kind},
    )

    # --- 2. Policy check ---
    policy = load_chat_policy()
    if not policy.enabled:
        yield ThreadRunnerEvent(event_type="error", content="Chat is disabled by policy.")
        return

    # --- 3. Persist user message ---
    oku, msgu, user_msg = append_message(user_key=user_key, thread_id=thread_id, role="user", content=user_message)
    if not oku or user_msg is None:
        logger.error("thread_runner.user_msg_failed user_key=%s error=%s", user_key, msgu)
        yield ThreadRunnerEvent(event_type="error", content=msgu or "Failed to save message.")
        return

    # --- 4. Load history ---
    history: List[ChatMsg] = []
    okh, _, hist_rows = list_messages(user_key=user_key, thread_id=thread_id, limit=12, before_seq=user_msg.seq)
    if okh:
        history = [ChatMsg(role=("user" if m.role == "user" else "assistant"), content=m.content) for m in hist_rows]
    logger.debug("thread_runner.history_loaded count=%d", len(history))

    # --- 5. Build stream ---
    reply_parts: List[str] = []
    tool_events_data: List[Any] = []

    if thr.kind == "case" and case_id:
        analysis_json = _load_analysis_json(case_id=case_id, run_id=run_id)
        if analysis_json:
            from agent.chat.runtime_streaming import run_chat_stream

            action_policy = load_action_policy()
            logger.info("thread_runner.runtime_dispatch kind=case case_id=%s run_id=%s", case_id, run_id)
            stream = run_chat_stream(
                policy=policy,
                action_policy=action_policy,
                analysis_json=analysis_json,
                user_message=user_message,
                history=history,
                case_id=case_id,
                run_id=run_id,
            )
        else:
            # Case exists but no analysis yet — fall back to global
            logger.warning("thread_runner.no_analysis case_id=%s falling_back=global", case_id)
            from agent.chat.global_runtime_streaming import run_global_chat_stream

            stream = run_global_chat_stream(policy=policy, user_message=user_message, history=history)
    else:
        logger.info("thread_runner.runtime_dispatch kind=global")
        from agent.chat.global_runtime_streaming import run_global_chat_stream

        stream = run_global_chat_stream(policy=policy, user_message=user_message, history=history)

    # --- 6. Stream events through ---
    async for event in stream:
        yield ThreadRunnerEvent(
            event_type=event.event_type,
            content=event.content,
            tool=getattr(event, "tool", None),
            metadata=getattr(event, "metadata", {}),
        )
        if event.event_type == "token":
            reply_parts.append(event.content)
        elif event.event_type == "done":
            tool_events_data = event.metadata.get("tool_events", [])

    # --- 7. Persist assistant reply + tool events ---
    reply = "".join(reply_parts) or "—"
    oka, msga, asst_msg = append_message(user_key=user_key, thread_id=thread_id, role="assistant", content=reply)
    if not oka or asst_msg is None:
        logger.warning("thread_runner.persist_failed error=%s", msga)
    else:
        logger.info(
            "thread_runner.persist_done reply_len=%d tool_events=%d",
            len(reply),
            len(tool_events_data),
        )
        if tool_events_data:
            try:
                insert_tool_events(
                    user_key=user_key,
                    thread_id=thread_id,
                    message_id=asst_msg.message_id,
                    tool_events=tool_events_data,
                )
            except Exception as e:
                logger.error("thread_runner.tool_events_failed error=%s", e)
