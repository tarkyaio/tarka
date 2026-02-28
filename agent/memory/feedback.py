from __future__ import annotations

from typing import Optional, Tuple

from agent.memory.config import build_postgres_dsn, load_memory_config


def _connect(dsn: str):
    import psycopg  # type: ignore[import-not-found]

    return psycopg.connect(dsn)


def record_skill_feedback(
    *,
    case_id: Optional[str],
    run_id: Optional[str],
    skill_id: Optional[str],
    outcome: str,
    notes: Optional[str] = None,
    actor: Optional[str] = None,
) -> Tuple[bool, str]:
    cfg = load_memory_config()
    dsn = build_postgres_dsn(cfg)
    if not dsn:
        return False, "Postgres not configured"

    outcome_txt = (outcome or "").strip()
    if not outcome_txt:
        return False, "outcome is required"

    with _connect(dsn) as conn:
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO skill_feedback(case_id, run_id, skill_id, outcome, notes, actor)
                VALUES (%s, %s, %s, %s, %s, %s);
                """,
                (
                    case_id,
                    run_id,
                    skill_id,
                    outcome_txt,
                    notes,
                    actor,
                ),
            )
    return True, "ok"
