from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from agent.memory.config import build_postgres_dsn, load_memory_config


def _connect(dsn: str):
    import psycopg  # type: ignore[import-not-found]

    return psycopg.connect(dsn)


@dataclass(frozen=True)
class ChatThread:
    thread_id: str
    user_key: str
    kind: str  # 'global' | 'case'
    case_id: Optional[str]
    title: Optional[str]
    created_at: str
    updated_at: str
    last_message_at: Optional[str]


@dataclass(frozen=True)
class ChatThreadPreview:
    thread: ChatThread
    last_message: Optional[Dict[str, Any]] = None  # {seq, role, content, created_at}


@dataclass(frozen=True)
class ChatMessage:
    message_id: str
    seq: int
    role: str
    content: str
    created_at: str


def _dsn() -> Optional[str]:
    cfg = load_memory_config()
    return build_postgres_dsn(cfg)


def _row_to_thread(row) -> ChatThread:
    return ChatThread(
        thread_id=str(row[0]),
        user_key=str(row[1]),
        kind=str(row[2]),
        case_id=str(row[3]) if row[3] else None,
        title=str(row[4]) if row[4] else None,
        created_at=str(row[5]),
        updated_at=str(row[6]),
        last_message_at=str(row[7]) if row[7] else None,
    )


def get_or_create_global_thread(*, user_key: str) -> Tuple[bool, str, Optional[ChatThread]]:
    dsn = _dsn()
    if not dsn:
        return False, "Postgres not configured", None
    uk = (user_key or "").strip().lower()
    if not uk:
        return False, "user_key_required", None
    with _connect(dsn) as conn:
        with conn.transaction():
            row = conn.execute(
                """
                INSERT INTO chat_threads(user_key, kind, case_id, title)
                VALUES (%s, 'global', NULL, NULL)
                ON CONFLICT (user_key) WHERE kind='global'
                DO UPDATE SET updated_at=now()
                RETURNING
                  thread_id::text, user_key, kind, case_id::text, title,
                  created_at::text, updated_at::text, last_message_at::text;
                """,
                (uk,),
            ).fetchone()
            if not row:
                return False, "db_error", None
            return True, "ok", _row_to_thread(row)


def get_or_create_case_thread(*, user_key: str, case_id: str) -> Tuple[bool, str, Optional[ChatThread]]:
    dsn = _dsn()
    if not dsn:
        return False, "Postgres not configured", None
    uk = (user_key or "").strip().lower()
    cid = (case_id or "").strip()
    if not uk:
        return False, "user_key_required", None
    if not cid:
        return False, "case_id_required", None
    with _connect(dsn) as conn:
        with conn.transaction():
            row = conn.execute(
                """
                INSERT INTO chat_threads(user_key, kind, case_id, title)
                VALUES (%s, 'case', %s, NULL)
                ON CONFLICT (user_key, case_id) WHERE kind='case'
                DO UPDATE SET updated_at=now()
                RETURNING
                  thread_id::text, user_key, kind, case_id::text, title,
                  created_at::text, updated_at::text, last_message_at::text;
                """,
                (uk, cid),
            ).fetchone()
            if not row:
                return False, "db_error", None
            return True, "ok", _row_to_thread(row)


def get_thread(*, user_key: str, thread_id: str) -> Tuple[bool, str, Optional[ChatThread]]:
    dsn = _dsn()
    if not dsn:
        return False, "Postgres not configured", None
    uk = (user_key or "").strip().lower()
    tid = (thread_id or "").strip()
    if not uk:
        return False, "user_key_required", None
    if not tid:
        return False, "thread_id_required", None
    with _connect(dsn) as conn:
        row = conn.execute(
            """
            SELECT
              thread_id::text, user_key, kind, case_id::text, title,
              created_at::text, updated_at::text, last_message_at::text
            FROM chat_threads
            WHERE thread_id::text = %s AND user_key = %s;
            """,
            (tid, uk),
        ).fetchone()
        if not row:
            return False, "not_found", None
        return True, "ok", _row_to_thread(row)


def list_threads(*, user_key: str, limit: int = 50) -> Tuple[bool, str, List[ChatThreadPreview]]:
    dsn = _dsn()
    if not dsn:
        return False, "Postgres not configured", []
    uk = (user_key or "").strip().lower()
    if not uk:
        return False, "user_key_required", []
    lim = max(1, min(int(limit), 200))
    with _connect(dsn) as conn:
        rows = conn.execute(
            """
            SELECT
              t.thread_id::text, t.user_key, t.kind, t.case_id::text, t.title,
              t.created_at::text, t.updated_at::text, t.last_message_at::text,
              lm.seq, lm.role, lm.content, lm.created_at::text
            FROM chat_threads t
            LEFT JOIN LATERAL (
              SELECT seq, role, content, created_at
              FROM chat_messages m
              WHERE m.thread_id = t.thread_id
              ORDER BY seq DESC
              LIMIT 1
            ) lm ON true
            WHERE t.user_key = %s
            ORDER BY COALESCE(t.last_message_at, t.updated_at) DESC
            LIMIT %s;
            """,
            (uk, lim),
        ).fetchall()
    out: List[ChatThreadPreview] = []
    for r in rows or []:
        thr = _row_to_thread(r[:8])
        last = None
        if r[8] is not None:
            last = {
                "seq": int(r[8]),
                "role": str(r[9] or ""),
                "content": str(r[10] or ""),
                "created_at": str(r[11] or ""),
            }
        out.append(ChatThreadPreview(thread=thr, last_message=last))
    return True, "ok", out


def list_messages(
    *,
    user_key: str,
    thread_id: str,
    limit: int = 50,
    before_seq: Optional[int] = None,
) -> Tuple[bool, str, List[ChatMessage]]:
    dsn = _dsn()
    if not dsn:
        return False, "Postgres not configured", []
    uk = (user_key or "").strip().lower()
    tid = (thread_id or "").strip()
    if not uk:
        return False, "user_key_required", []
    if not tid:
        return False, "thread_id_required", []
    lim = max(1, min(int(limit), 200))
    bseq = int(before_seq) if before_seq is not None else None
    with _connect(dsn) as conn:
        rows = conn.execute(
            """
            SELECT
              m.message_id::text, m.seq, m.role, m.content, m.created_at::text
            FROM chat_messages m
            INNER JOIN chat_threads t ON t.thread_id = m.thread_id
            WHERE t.user_key = %s
              AND t.thread_id::text = %s
              AND (%s::int IS NULL OR m.seq < %s::int)
            ORDER BY m.seq DESC
            LIMIT %s;
            """,
            (uk, tid, bseq, bseq, lim),
        ).fetchall()
    msgs: List[ChatMessage] = []
    for r in rows or []:
        msgs.append(
            ChatMessage(
                message_id=str(r[0]),
                seq=int(r[1]),
                role=str(r[2] or ""),
                content=str(r[3] or ""),
                created_at=str(r[4] or ""),
            )
        )
    # Return ascending for UI rendering.
    msgs.reverse()
    return True, "ok", msgs


def append_message(
    *, user_key: str, thread_id: str, role: str, content: str
) -> Tuple[bool, str, Optional[ChatMessage]]:
    dsn = _dsn()
    if not dsn:
        return False, "Postgres not configured", None
    uk = (user_key or "").strip().lower()
    tid = (thread_id or "").strip()
    rl = (role or "").strip().lower()
    txt = str(content or "")
    if not uk:
        return False, "user_key_required", None
    if not tid:
        return False, "thread_id_required", None
    if rl not in ("user", "assistant"):
        return False, "invalid_role", None
    if not txt.strip():
        return False, "content_required", None

    with _connect(dsn) as conn:
        with conn.transaction():
            # Lock the thread row so seq assignment is concurrency-safe per thread.
            row = conn.execute(
                "SELECT thread_id::text FROM chat_threads WHERE thread_id::text=%s AND user_key=%s FOR UPDATE;",
                (tid, uk),
            ).fetchone()
            if not row:
                return False, "not_found", None

            cur = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM chat_messages WHERE thread_id::text=%s;",
                (tid,),
            ).fetchone()
            next_seq = int(cur[0] or 0) + 1

            ins = conn.execute(
                """
                INSERT INTO chat_messages(thread_id, seq, role, content)
                VALUES (%s, %s, %s, %s)
                RETURNING message_id::text, seq, role, content, created_at::text;
                """,
                (tid, next_seq, rl, txt),
            ).fetchone()
            if not ins:
                return False, "db_error", None

            conn.execute(
                "UPDATE chat_threads SET updated_at=now(), last_message_at=now() WHERE thread_id::text=%s;",
                (tid,),
            )

            return (
                True,
                "ok",
                ChatMessage(
                    message_id=str(ins[0]),
                    seq=int(ins[1]),
                    role=str(ins[2]),
                    content=str(ins[3]),
                    created_at=str(ins[4]),
                ),
            )


def insert_tool_events(
    *,
    user_key: str,
    thread_id: str,
    message_id: Optional[str],
    tool_events: List[Dict[str, Any]],
) -> Tuple[bool, str]:
    dsn = _dsn()
    if not dsn:
        return False, "Postgres not configured"
    uk = (user_key or "").strip().lower()
    tid = (thread_id or "").strip()
    if not uk:
        return False, "user_key_required"
    if not tid:
        return False, "thread_id_required"
    if not tool_events:
        return True, "ok"

    with _connect(dsn) as conn:
        with conn.transaction():
            # Ensure thread belongs to user.
            row = conn.execute(
                "SELECT thread_id::text FROM chat_threads WHERE thread_id::text=%s AND user_key=%s;",
                (tid, uk),
            ).fetchone()
            if not row:
                return False, "not_found"

            for ev in tool_events[:50]:
                if not isinstance(ev, dict):
                    continue
                tool = str(ev.get("tool") or "").strip()
                if not tool:
                    continue
                conn.execute(
                    """
                    INSERT INTO chat_tool_events(thread_id, message_id, tool, args, ok, result, error)
                    VALUES (%s, %s, %s, %s::jsonb, %s, %s::jsonb, %s);
                    """,
                    (
                        tid,
                        (str(message_id) if message_id else None),
                        tool,
                        json.dumps(ev.get("args") or {}),
                        bool(ev.get("ok")),
                        json.dumps(ev.get("result")) if ev.get("result") is not None else None,
                        (str(ev.get("error")) if ev.get("error") else None),
                    ),
                )
    return True, "ok"
