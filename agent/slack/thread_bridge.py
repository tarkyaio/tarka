"""
Slack thread ↔ Tarka chat thread mapping.

Maps (slack_channel, slack_thread_ts) → (tarka_thread_id, case_id) so that
replies in a Slack notification thread are automatically scoped to the right case.

An in-memory dict serves as a fallback when Postgres is not configured, so
threads work within a single process even without a DB.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from agent.memory.config import build_postgres_dsn, load_memory_config

logger = logging.getLogger(__name__)

# In-memory fallback: (channel, thread_ts) → (tarka_thread_id, case_id)
_in_memory: Dict[Tuple[str, str], Tuple[Optional[str], Optional[str]]] = {}


def _connect(dsn: str):
    import psycopg  # type: ignore[import-not-found]

    return psycopg.connect(dsn)


def _dsn() -> Optional[str]:
    cfg = load_memory_config()
    return build_postgres_dsn(cfg)


@dataclass(frozen=True)
class SlackThreadMapping:
    slack_channel: str
    slack_thread_ts: str
    tarka_thread_id: Optional[str]
    case_id: Optional[str]


def register_notification_thread(
    *,
    slack_channel: str,
    slack_thread_ts: str,
    case_id: Optional[str] = None,
    tarka_thread_id: Optional[str] = None,
) -> bool:
    """
    Register a Slack thread as linked to a Tarka case.

    Called by the notifier after posting a notification.
    Always succeeds: writes to Postgres when configured, otherwise falls back
    to the in-memory store so threads work within the current process.
    Returns True on success.
    """
    key = (slack_channel, slack_thread_ts)
    existing_tid, existing_cid = _in_memory.get(key, (None, None))
    _in_memory[key] = (
        tarka_thread_id or existing_tid,
        case_id or existing_cid,
    )

    dsn = _dsn()
    if not dsn:
        return True
    try:
        with _connect(dsn) as conn:
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO slack_thread_map (slack_channel, slack_thread_ts, tarka_thread_id, case_id)
                    VALUES (%s, %s, %s, %s::uuid)
                    ON CONFLICT (slack_channel, slack_thread_ts)
                    DO UPDATE SET
                        tarka_thread_id = COALESCE(EXCLUDED.tarka_thread_id, slack_thread_map.tarka_thread_id),
                        case_id = COALESCE(EXCLUDED.case_id, slack_thread_map.case_id)
                    """,
                    (slack_channel, slack_thread_ts, tarka_thread_id, case_id),
                )
        return True
    except Exception as e:
        logger.warning("Failed to persist Slack thread mapping to DB (in-memory fallback active): %s", e)
        return True


def lookup_thread(
    *,
    slack_channel: str,
    slack_thread_ts: str,
) -> Optional[SlackThreadMapping]:
    """
    Look up a Slack thread → Tarka mapping.

    Checks in-memory store first (always available), then Postgres if configured.
    Returns None if no mapping exists.
    """
    key = (slack_channel, slack_thread_ts)

    dsn = _dsn()
    if dsn:
        try:
            with _connect(dsn) as conn:
                row = conn.execute(
                    """
                    SELECT slack_channel, slack_thread_ts, tarka_thread_id::text, case_id::text
                    FROM slack_thread_map
                    WHERE slack_channel = %s AND slack_thread_ts = %s
                    """,
                    (slack_channel, slack_thread_ts),
                ).fetchone()
                if row:
                    return SlackThreadMapping(
                        slack_channel=str(row[0]),
                        slack_thread_ts=str(row[1]),
                        tarka_thread_id=str(row[2]) if row[2] else None,
                        case_id=str(row[3]) if row[3] else None,
                    )
        except Exception as e:
            logger.warning("Failed to look up Slack thread mapping from DB, falling back to memory: %s", e)

    if key in _in_memory:
        tid, cid = _in_memory[key]
        return SlackThreadMapping(
            slack_channel=slack_channel,
            slack_thread_ts=slack_thread_ts,
            tarka_thread_id=tid,
            case_id=cid,
        )

    return None


def update_tarka_thread_id(
    *,
    slack_channel: str,
    slack_thread_ts: str,
    tarka_thread_id: str,
) -> bool:
    """Update the tarka_thread_id for an existing mapping."""
    key = (slack_channel, slack_thread_ts)
    _, existing_cid = _in_memory.get(key, (None, None))
    _in_memory[key] = (tarka_thread_id, existing_cid)

    dsn = _dsn()
    if not dsn:
        return True
    try:
        with _connect(dsn) as conn:
            with conn.transaction():
                conn.execute(
                    """
                    UPDATE slack_thread_map
                    SET tarka_thread_id = %s
                    WHERE slack_channel = %s AND slack_thread_ts = %s
                    """,
                    (tarka_thread_id, slack_channel, slack_thread_ts),
                )
        return True
    except Exception as e:
        logger.warning("Failed to update Slack thread mapping in DB: %s", e)
        return True
