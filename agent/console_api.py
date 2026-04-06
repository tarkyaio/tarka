"""
FastAPI router for executive / leadership dashboard endpoints.

Mounted on the main `app` in agent/api/webhook.py.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()


@router.get("/api/v1/exec/overview")
def exec_overview(
    days: int = Query(30, ge=1, le=365, description="Rolling window in days"),
    top_n: int = Query(5, ge=1, le=20, description="Max items in ranked lists"),
    stale_minutes: int = Query(60, ge=1, description="Minutes before an open case is considered stale"),
    high_impact_threshold: int = Query(85, ge=0, le=100, description="Impact score threshold for high-impact"),
    triage_minutes_assumed: float = Query(20.0, ge=1, description="Assumed manual triage minutes per alert"),
    hourly_rate_usd_assumed: float = Query(85.0, ge=1, description="Assumed engineer hourly rate (USD)"),
) -> Dict[str, Any]:
    """
    Executive overview for the leadership dashboard.

    Aggregates signal quality, ROI savings, MTTR trends, top services,
    recurrence, and AI cost from the Postgres case history.
    """
    from agent.api.webhook import _get_db_connection
    from agent.memory.console_queries import get_exec_overview

    conn = _get_db_connection()
    if not conn:
        raise HTTPException(status_code=503, detail="Postgres not configured")

    try:
        return get_exec_overview(
            conn,
            days=days,
            top_n=top_n,
            stale_minutes=stale_minutes,
            high_impact_threshold=high_impact_threshold,
            triage_minutes_assumed=triage_minutes_assumed,
            hourly_rate_usd_assumed=hourly_rate_usd_assumed,
        )
    finally:
        conn.close()
