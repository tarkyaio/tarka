from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from agent.memory.config import build_postgres_dsn, load_memory_config


def _connect(dsn: str):
    import psycopg  # type: ignore[import-not-found]

    return psycopg.connect(dsn)


@dataclass(frozen=True)
class CaseAction:
    action_id: str
    case_id: str
    run_id: Optional[str]
    created_at: str
    updated_at: str
    status: str
    hypothesis_id: Optional[str]
    action_type: str
    title: str
    risk: Optional[str]
    preconditions: List[str]
    execution_payload: Dict[str, Any]
    proposed_by: Optional[str]
    approved_at: Optional[str]
    approved_by: Optional[str]
    approval_notes: Optional[str]
    executed_at: Optional[str]
    executed_by: Optional[str]
    execution_notes: Optional[str]


def create_case_action(
    *,
    case_id: str,
    run_id: Optional[str],
    hypothesis_id: Optional[str],
    action_type: str,
    title: str,
    risk: Optional[str],
    preconditions: Optional[List[str]],
    execution_payload: Optional[Dict[str, Any]],
    proposed_by: Optional[str],
) -> Tuple[bool, str, Optional[str]]:
    cfg = load_memory_config()
    dsn = build_postgres_dsn(cfg)
    if not dsn:
        return False, "Postgres not configured", None

    atype = (action_type or "").strip().lower()
    ttl = (title or "").strip()
    if not atype:
        return False, "action_type_required", None
    if not ttl:
        return False, "title_required", None

    with _connect(dsn) as conn:
        with conn.transaction():
            row = conn.execute(
                """
                INSERT INTO case_actions(
                  case_id, run_id, hypothesis_id, action_type, title, risk, preconditions, execution_payload, proposed_by
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                RETURNING action_id::text;
                """,
                (
                    str(case_id),
                    str(run_id) if run_id else None,
                    str(hypothesis_id) if hypothesis_id else None,
                    atype,
                    ttl,
                    (risk or None),
                    (preconditions or []),
                    (execution_payload or {}),
                    (proposed_by or None),
                ),
            ).fetchone()
            return True, "ok", (str(row[0]) if row and row[0] else None)


def list_case_actions(*, case_id: str, limit: int = 50) -> Tuple[bool, str, List[CaseAction]]:
    cfg = load_memory_config()
    dsn = build_postgres_dsn(cfg)
    if not dsn:
        return False, "Postgres not configured", []

    lim = max(1, min(int(limit), 500))
    with _connect(dsn) as conn:
        rows = conn.execute(
            """
            SELECT
              action_id::text,
              case_id::text,
              run_id::text,
              created_at::text,
              updated_at::text,
              status,
              hypothesis_id,
              action_type,
              title,
              risk,
              COALESCE(preconditions, '[]'::jsonb),
              COALESCE(execution_payload, '{}'::jsonb),
              proposed_by,
              approved_at::text,
              approved_by,
              approval_notes,
              executed_at::text,
              executed_by,
              execution_notes
            FROM case_actions
            WHERE case_id::text = %s
            ORDER BY created_at DESC
            LIMIT %s;
            """,
            (str(case_id), lim),
        ).fetchall()

    out: List[CaseAction] = []
    for r in rows or []:
        pre = r[10] if isinstance(r[10], list) else (list(r[10]) if r[10] else [])
        payload = r[11] if isinstance(r[11], dict) else {}
        out.append(
            CaseAction(
                action_id=str(r[0]),
                case_id=str(r[1]),
                run_id=str(r[2]) if r[2] else None,
                created_at=str(r[3]),
                updated_at=str(r[4]),
                status=str(r[5] or ""),
                hypothesis_id=str(r[6]) if r[6] else None,
                action_type=str(r[7] or ""),
                title=str(r[8] or ""),
                risk=str(r[9]) if r[9] else None,
                preconditions=[str(x) for x in (pre or [])],
                execution_payload=payload,
                proposed_by=str(r[12]) if r[12] else None,
                approved_at=str(r[13]) if r[13] else None,
                approved_by=str(r[14]) if r[14] else None,
                approval_notes=str(r[15]) if r[15] else None,
                executed_at=str(r[16]) if r[16] else None,
                executed_by=str(r[17]) if r[17] else None,
                execution_notes=str(r[18]) if r[18] else None,
            )
        )
    return True, "ok", out


def transition_case_action(
    *,
    case_id: str,
    action_id: str,
    status: str,
    actor: Optional[str],
    notes: Optional[str],
) -> Tuple[bool, str]:
    cfg = load_memory_config()
    dsn = build_postgres_dsn(cfg)
    if not dsn:
        return False, "Postgres not configured"

    st = (status or "").strip().lower()
    if st not in ("approved", "rejected", "executed"):
        return False, "invalid_status"

    with _connect(dsn) as conn:
        with conn.transaction():
            # Ensure action belongs to case
            row = conn.execute(
                "SELECT status FROM case_actions WHERE action_id::text = %s AND case_id::text = %s;",
                (str(action_id), str(case_id)),
            ).fetchone()
            if not row:
                return False, "not_found"
            prev = str(row[0] or "")
            if prev == "executed":
                return False, "already_executed"
            if prev == "rejected" and st != "rejected":
                return False, "rejected_immutable"

            if st == "approved":
                conn.execute(
                    """
                    UPDATE case_actions
                    SET status='approved', updated_at=now(), approved_at=now(), approved_by=%s, approval_notes=%s
                    WHERE action_id::text=%s AND case_id::text=%s;
                    """,
                    (actor, notes, str(action_id), str(case_id)),
                )
            elif st == "rejected":
                conn.execute(
                    """
                    UPDATE case_actions
                    SET status='rejected', updated_at=now(), approved_at=now(), approved_by=%s, approval_notes=%s
                    WHERE action_id::text=%s AND case_id::text=%s;
                    """,
                    (actor, notes, str(action_id), str(case_id)),
                )
            else:
                conn.execute(
                    """
                    UPDATE case_actions
                    SET status='executed', updated_at=now(), executed_at=now(), executed_by=%s, execution_notes=%s
                    WHERE action_id::text=%s AND case_id::text=%s;
                    """,
                    (actor, notes, str(action_id), str(case_id)),
                )
    return True, "ok"
