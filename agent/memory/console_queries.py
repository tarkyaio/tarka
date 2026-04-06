"""
SQL queries that power the /api/v1/exec/overview leadership dashboard.

All queries run against the Postgres `cases` / `investigation_runs` tables.
"""

from __future__ import annotations

from typing import Any


def get_exec_overview(
    conn,
    *,
    days: int = 30,
    top_n: int = 5,
    stale_minutes: int = 60,
    high_impact_threshold: int = 85,
    triage_minutes_assumed: float = 20.0,
    hourly_rate_usd_assumed: float = 85.0,
) -> dict[str, Any]:
    """
    Compute the full exec overview payload from Postgres.

    Returns a dict matching the ExecOverviewResponse shape expected by the UI.
    All time-bounded queries use a rolling window of ``days`` days.
    """
    risk = _query_risk(
        conn, days=days, stale_minutes=stale_minutes, high_impact_threshold=high_impact_threshold, top_n=top_n
    )
    trends = _query_trends(conn, days=days)
    focus = _query_focus(conn, days=days, high_impact_threshold=high_impact_threshold, top_n=top_n)
    recurrence = _query_recurrence(conn, days=days, top_n=top_n)
    ai = _query_ai(conn, days=days)
    signal = _query_signal(conn, days=days)
    cost = _query_cost(conn, days=days)
    savings = _compute_savings(
        signal=signal,
        triage_minutes_assumed=triage_minutes_assumed,
        hourly_rate_usd_assumed=hourly_rate_usd_assumed,
    )

    return {
        "risk": risk,
        "trends": trends,
        "focus": focus,
        "recurrence": recurrence,
        "ai": ai,
        "signal": signal,
        "savings": savings,
        "cost": cost,
    }


# ---------------------------------------------------------------------------
# Section helpers
# ---------------------------------------------------------------------------


def _query_risk(conn, *, days: int, stale_minutes: int, high_impact_threshold: int, top_n: int) -> dict:
    # ---- aggregate counts for open (non-snoozed) cases ----
    summary_row = conn.execute(f"""
        WITH latest_runs AS (
            SELECT DISTINCT ON (r.case_id)
                r.case_id,
                NULLIF(r.analysis_json #>> '{{analysis,scores,impact_score}}', '')::int AS impact_score
            FROM investigation_runs r
            INNER JOIN cases c ON r.case_id = c.case_id
            WHERE c.status = 'open'
              AND (c.snoozed_until IS NULL OR c.snoozed_until <= now())
            ORDER BY r.case_id, r.created_at DESC
        )
        SELECT
            COUNT(*)                                                                       AS active_count,
            COUNT(*) FILTER (WHERE lr.impact_score >= {high_impact_threshold})            AS active_high_impact_count,
            COUNT(*) FILTER (WHERE c.updated_at <= now() - INTERVAL '{stale_minutes} minutes') AS stale_investigation_count,
            MIN(c.created_at)::text                                                       AS oldest_active_created_at
        FROM cases c
        INNER JOIN latest_runs lr ON c.case_id = lr.case_id
        WHERE c.status = 'open'
          AND (c.snoozed_until IS NULL OR c.snoozed_until <= now())
        """).fetchone()

    # ---- this-month totals (across all cases, not just open) ----
    month_row = conn.execute(f"""
        WITH latest_runs AS (
            SELECT DISTINCT ON (r.case_id)
                r.case_id,
                NULLIF(r.analysis_json #>> '{{analysis,scores,impact_score}}', '')::int AS impact_score
            FROM investigation_runs r
            ORDER BY r.case_id, r.created_at DESC
        )
        SELECT
            COUNT(*)                                                         AS total_this_month,
            COUNT(*) FILTER (WHERE lr.impact_score >= {high_impact_threshold}) AS critical_this_month
        FROM cases c
        INNER JOIN latest_runs lr ON c.case_id = lr.case_id
        WHERE date_trunc('month', c.created_at) = date_trunc('month', now())
        """).fetchone()

    # ---- top active open cases by impact score ----
    top_rows = conn.execute("""
        SELECT DISTINCT ON (r.case_id)
            c.case_id::text                                                           AS incident_id,
            c.created_at::text                                                        AS created_at,
            r.alertname,
            NULLIF(r.analysis_json #>> '{analysis,verdict,one_liner}', '')          AS one_liner,
            NULLIF(r.analysis_json #>> '{target,team}', '')                         AS team,
            COALESCE(NULLIF(r.service, ''), NULLIF(r.workload_name, ''))              AS service,
            NULLIF(r.analysis_json #>> '{analysis,features,family}', '')            AS family,
            NULLIF(r.analysis_json #>> '{analysis,scores,impact_score}', '')::int   AS impact_score,
            NULLIF(r.analysis_json #>> '{analysis,scores,confidence_score}', '')::int AS confidence_score
        FROM cases c
        INNER JOIN investigation_runs r ON r.case_id = c.case_id
        WHERE c.status = 'open'
          AND (c.snoozed_until IS NULL OR c.snoozed_until <= now())
        ORDER BY r.case_id, r.created_at DESC
        """).fetchall()

    # sort by impact_score desc, take top_n
    top_rows_sorted = sorted(
        top_rows,
        key=lambda r: (r[7] or 0),
        reverse=True,
    )[:top_n]

    return {
        "active_count": int(summary_row[0] or 0),
        "active_high_impact_count": int(summary_row[1] or 0),
        "stale_investigation_count": int(summary_row[2] or 0),
        "oldest_active_created_at": summary_row[3],
        "critical_this_month": int(month_row[1] or 0),
        "total_this_month": int(month_row[0] or 0),
        "top_active": [
            {
                "incident_id": str(r[0]),
                "created_at": str(r[1]),
                "alertname": r[2],
                "one_liner": r[3],
                "team": r[4],
                "service": r[5],
                "family": r[6],
                "impact_score": r[7],
                "confidence_score": r[8],
            }
            for r in top_rows_sorted
        ],
    }


def _query_trends(conn, *, days: int) -> dict:
    # ---- daily: cases created per day + median impact of their latest run ----
    daily_rows = conn.execute(f"""
        WITH latest_runs AS (
            SELECT DISTINCT ON (r.case_id)
                r.case_id,
                NULLIF(r.analysis_json #>> '{{analysis,scores,impact_score}}', '')::int AS impact_score
            FROM investigation_runs r
            ORDER BY r.case_id, r.created_at DESC
        )
        SELECT
            c.created_at::date::text                                                        AS day,
            COUNT(*)                                                                        AS incidents_created,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY lr.impact_score NULLS LAST)        AS impact_median
        FROM cases c
        LEFT JOIN latest_runs lr ON c.case_id = lr.case_id
        WHERE c.created_at >= now() - INTERVAL '{days} days'
        GROUP BY 1
        ORDER BY 1
        """).fetchall()

    # ---- MTTR weekly: use max(days, 56) so we always show ~8 weeks ----
    mttr_days = max(days, 56)
    mttr_rows = conn.execute(f"""
        SELECT
            date_trunc('week', c.created_at)::date::text                                              AS week,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY
                EXTRACT(EPOCH FROM (c.resolved_at - c.created_at)) / 3600.0 NULLS LAST)              AS mttr_hours_median,
            COUNT(*)                                                                                   AS resolved_count
        FROM cases c
        WHERE c.status = 'closed'
          AND c.resolved_at IS NOT NULL
          AND c.created_at >= now() - INTERVAL '{mttr_days} days'
        GROUP BY 1
        ORDER BY 1
        """).fetchall()

    return {
        "daily": [
            {
                "day": str(r[0]),
                "incidents_created": int(r[1]),
                "impact_median": round(float(r[2]), 1) if r[2] is not None else None,
            }
            for r in daily_rows
        ],
        "mttr_weekly": [
            {
                "week": str(r[0]),
                "mttr_hours_median": round(float(r[1]), 2) if r[1] is not None else None,
                "resolved_count": int(r[2]),
            }
            for r in mttr_rows
        ],
    }


def _query_focus(conn, *, days: int, high_impact_threshold: int, top_n: int) -> dict:
    # ---- top teams by active open case count ----
    team_rows = conn.execute(f"""
        WITH latest_runs AS (
            SELECT DISTINCT ON (r.case_id)
                r.case_id,
                NULLIF(r.analysis_json #>> '{{target,team}}', '')                          AS team,
                NULLIF(r.analysis_json #>> '{{analysis,scores,impact_score}}', '')::int    AS impact_score
            FROM investigation_runs r
            INNER JOIN cases c ON r.case_id = c.case_id
            WHERE c.status = 'open'
              AND (c.snoozed_until IS NULL OR c.snoozed_until <= now())
            ORDER BY r.case_id, r.created_at DESC
        )
        SELECT
            team,
            COUNT(*)                                                                AS active_count,
            COUNT(*) FILTER (WHERE impact_score >= {high_impact_threshold})         AS high_impact_count,
            COALESCE(ROUND(AVG(impact_score)::numeric), 0)::int                    AS total_impact
        FROM latest_runs
        WHERE team IS NOT NULL
        GROUP BY team
        ORDER BY active_count DESC, total_impact DESC
        LIMIT {top_n}
        """).fetchall()

    # ---- top drivers by active open case count ----
    driver_rows = conn.execute(f"""
        WITH latest_runs AS (
            SELECT DISTINCT ON (r.case_id)
                r.case_id,
                NULLIF(r.analysis_json #>> '{{analysis,verdict,primary_driver}}', '')      AS driver,
                NULLIF(r.analysis_json #>> '{{analysis,scores,impact_score}}', '')::int    AS impact_score
            FROM investigation_runs r
            INNER JOIN cases c ON r.case_id = c.case_id
            WHERE c.status = 'open'
              AND (c.snoozed_until IS NULL OR c.snoozed_until <= now())
            ORDER BY r.case_id, r.created_at DESC
        )
        SELECT
            driver,
            COUNT(*)                                                                AS active_count,
            COUNT(*) FILTER (WHERE impact_score >= {high_impact_threshold})         AS high_impact_count,
            COALESCE(ROUND(AVG(impact_score)::numeric), 0)::int                    AS total_impact
        FROM latest_runs
        WHERE driver IS NOT NULL
        GROUP BY driver
        ORDER BY active_count DESC, total_impact DESC
        LIMIT {top_n}
        """).fetchall()

    # ---- top services by run volume in the rolling window ----
    service_rows = conn.execute(f"""
        SELECT
            COALESCE(NULLIF(r.service, ''), NULLIF(r.workload_name, ''), 'unknown')        AS service,
            COUNT(DISTINCT r.case_id)                                                       AS incident_count,
            COUNT(DISTINCT r.alertname)                                                     AS unique_alert_types,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY
                NULLIF(r.analysis_json #>> '{{analysis,scores,impact_score}}', '')::int
                NULLS LAST)                                                                 AS median_impact,
            COUNT(*) FILTER (WHERE
                NULLIF(r.analysis_json #>> '{{analysis,change,has_recent_change}}', '')::bool = true) AS change_correlated_count
        FROM investigation_runs r
        WHERE r.created_at >= now() - INTERVAL '{days} days'
        GROUP BY 1
        ORDER BY incident_count DESC, median_impact DESC NULLS LAST
        LIMIT {top_n}
        """).fetchall()

    return {
        "top_teams": [
            {
                "team": str(r[0]),
                "active_count": int(r[1]),
                "high_impact_count": int(r[2]),
                "total_impact": int(r[3]),
            }
            for r in team_rows
        ],
        "top_drivers": [
            {
                "driver": str(r[0]),
                "active_count": int(r[1]),
                "high_impact_count": int(r[2]),
                "total_impact": int(r[3]),
            }
            for r in driver_rows
        ],
        "top_services": [
            {
                "service": str(r[0]),
                "incident_count": int(r[1]),
                "unique_alert_types": int(r[2]),
                "median_impact": round(float(r[3]), 1) if r[3] is not None else None,
                "change_correlated_count": int(r[4]),
            }
            for r in service_rows
        ],
    }


def _query_recurrence(conn, *, days: int, top_n: int) -> dict:
    # recurrence rate: fraction of cases in the window that re-fired (>1 run)
    rate_row = conn.execute(f"""
        SELECT
            COUNT(*) FILTER (WHERE run_count > 1)::float / NULLIF(COUNT(*), 0) AS recurrence_rate
        FROM (
            SELECT case_id, COUNT(*) AS run_count
            FROM investigation_runs
            WHERE created_at >= now() - INTERVAL '{days} days'
            GROUP BY case_id
        ) sub
        """).fetchone()

    top_rows = conn.execute(f"""
        SELECT
            r.alertname || ':' || COALESCE(NULLIF(r.service, ''), NULLIF(r.workload_name, ''), 'unknown') AS incident_key,
            COUNT(DISTINCT r.case_id) AS count
        FROM investigation_runs r
        WHERE r.created_at >= now() - INTERVAL '{days} days'
        GROUP BY incident_key
        HAVING COUNT(DISTINCT r.case_id) > 1
        ORDER BY count DESC
        LIMIT {top_n}
        """).fetchall()

    return {
        "rate": round(float(rate_row[0] or 0), 4),
        "top": [{"incident_key": str(r[0]), "count": int(r[1])} for r in top_rows],
    }


def _query_ai(conn, *, days: int) -> dict:
    # TTFA (time-to-first-analysis): only rows where starts_at is valid and run completed after alert fired.
    # TTFA: only first run per case (re-runs inherit the original starts_at and would
    # inflate the metric), and only when the alert was fresh when received (≤ 30 min old).
    # This measures Tarka's pipeline latency, not how long an alert had been firing.
    ttfa_row = conn.execute(f"""
        WITH first_runs AS (
            SELECT DISTINCT ON (r.case_id)
                r.case_id,
                r.created_at,
                r.starts_at
            FROM investigation_runs r
            WHERE r.created_at >= now() - INTERVAL '{days} days'
              AND r.starts_at IS NOT NULL
              AND r.starts_at <> ''
            ORDER BY r.case_id, r.created_at ASC
        )
        SELECT
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY
                EXTRACT(EPOCH FROM (fr.created_at - fr.starts_at::timestamptz)) NULLS LAST) AS ttfa_median_seconds,
            PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY
                EXTRACT(EPOCH FROM (fr.created_at - fr.starts_at::timestamptz)) NULLS LAST) AS ttfa_p90_seconds
        FROM first_runs fr
        WHERE fr.created_at > fr.starts_at::timestamptz
          AND fr.created_at - fr.starts_at::timestamptz <= INTERVAL '30 minutes'
        """).fetchone()

    # Data-completeness gaps: count across ALL runs in the window regardless of TTFA availability.
    gaps_row = conn.execute(f"""
        SELECT
            COUNT(*) FILTER (WHERE
                NULLIF(r.analysis_json #>> '{{analysis,scores,confidence_score}}', '')::int >= 70
            )::float * 100.0 / NULLIF(COUNT(*), 0)                                        AS confidence_ge_70_pct,
            COUNT(*) FILTER (WHERE NULLIF(r.one_liner, '') IS NULL
            )::float * 100.0 / NULLIF(COUNT(*), 0)                                        AS missing_one_liner_pct,
            COUNT(*) FILTER (WHERE NULLIF(r.analysis_json #>> '{{target,team}}', '') IS NULL
            )::float * 100.0 / NULLIF(COUNT(*), 0)                                        AS missing_team_pct,
            COUNT(*) FILTER (WHERE NULLIF(r.analysis_json #>> '{{analysis,features,family}}', '') IS NULL
            )::float * 100.0 / NULLIF(COUNT(*), 0)                                        AS missing_family_pct
        FROM investigation_runs r
        WHERE r.created_at >= now() - INTERVAL '{days} days'
        """).fetchone()

    return {
        "ttfa_median_seconds": round(float(ttfa_row[0]), 1) if ttfa_row[0] is not None else None,
        "ttfa_p90_seconds": round(float(ttfa_row[1]), 1) if ttfa_row[1] is not None else None,
        "confidence_ge_70_pct": round(float(gaps_row[0] or 0), 1),
        "gaps_pct": {
            "missing_one_liner": round(float(gaps_row[1] or 0), 1),
            "missing_team": round(float(gaps_row[2] or 0), 1),
            "missing_family": round(float(gaps_row[3] or 0), 1),
        },
    }


def _query_signal(conn, *, days: int) -> dict:
    row = conn.execute(f"""
        SELECT
            COUNT(*)                                                                                   AS total_runs,
            COUNT(*) FILTER (WHERE r.classification = 'actionable')                                   AS actionable,
            COUNT(*) FILTER (WHERE r.classification = 'noisy')                                        AS noisy,
            COUNT(*) FILTER (WHERE r.classification = 'informational')                                AS informational,
            COUNT(*) FILTER (WHERE
                r.classification IS NULL
                OR r.classification NOT IN ('actionable', 'noisy', 'informational'))                  AS unclassified,
            COUNT(*) FILTER (WHERE
                NULLIF(r.analysis_json #>> '{{analysis,change,has_recent_change}}', '')::bool = true) AS change_correlated_count,
            COUNT(*) FILTER (WHERE
                NULLIF(r.analysis_json #>> '{{analysis,scores,confidence_score}}', '')::int >= 70)    AS high_conf_runs
        FROM investigation_runs r
        WHERE r.created_at >= now() - INTERVAL '{days} days'
        """).fetchone()

    total = int(row[0] or 0)
    actionable = int(row[1] or 0)
    change_correlated = int(row[5] or 0)
    high_conf_runs = int(row[6] or 0)

    return {
        "total_runs": total,
        "actionable": actionable,
        "noisy": int(row[2] or 0),
        "informational": int(row[3] or 0),
        "unclassified": int(row[4] or 0),
        "actionable_pct": round(actionable * 100.0 / total, 1) if total else 0.0,
        "change_correlated_count": change_correlated,
        "change_correlated_pct": round(change_correlated * 100.0 / total, 1) if total else 0.0,
        "high_conf_runs": high_conf_runs,
    }


def _compute_savings(
    *,
    signal: dict,
    triage_minutes_assumed: float,
    hourly_rate_usd_assumed: float,
) -> dict:
    total_runs = signal["total_runs"]
    actionable_runs = signal["actionable"]
    high_conf_runs = signal["high_conf_runs"]
    noisy_runs = signal["noisy"]
    # deflected = runs where the agent handled triage (actionable + noisy)
    deflected_runs = actionable_runs + noisy_runs
    low_conf_runs = total_runs - high_conf_runs
    hours_saved = round(deflected_runs * triage_minutes_assumed / 60.0, 2)
    cost_saved_usd = round(hours_saved * hourly_rate_usd_assumed, 2)

    return {
        "total_runs": total_runs,
        "high_conf_runs": high_conf_runs,
        "low_conf_runs": low_conf_runs,
        "actionable_runs": actionable_runs,
        "deflected_runs": deflected_runs,
        "hours_saved": hours_saved,
        "cost_saved_usd": cost_saved_usd,
        "triage_minutes_assumed": triage_minutes_assumed,
        "hourly_rate_usd_assumed": hourly_rate_usd_assumed,
    }


def _query_cost(conn, *, days: int) -> dict:
    summary_row = conn.execute(f"""
        SELECT
            COALESCE(SUM(
                (r.analysis_json #>> '{{analysis,llm,usage,estimated_cost_usd}}')::float
            ), 0)                                                                       AS total_usd,
            AVG(
                (r.analysis_json #>> '{{analysis,llm,usage,estimated_cost_usd}}')::float
            )                                                                           AS avg_per_run_usd,
            COUNT(*)                                                                    AS total_runs
        FROM investigation_runs r
        WHERE r.created_at >= now() - INTERVAL '{days} days'
          AND r.analysis_json #>> '{{analysis,llm,usage,estimated_cost_usd}}' IS NOT NULL
        """).fetchone()

    daily_rows = conn.execute(f"""
        SELECT
            r.created_at::date::text                                                    AS day,
            COALESCE(SUM(
                (r.analysis_json #>> '{{analysis,llm,usage,estimated_cost_usd}}')::float
            ), 0)                                                                       AS cost_usd
        FROM investigation_runs r
        WHERE r.created_at >= now() - INTERVAL '{days} days'
          AND r.analysis_json #>> '{{analysis,llm,usage,estimated_cost_usd}}' IS NOT NULL
        GROUP BY 1
        ORDER BY 1
        """).fetchall()

    return {
        "total_usd": round(float(summary_row[0] or 0), 6),
        "avg_per_run_usd": round(float(summary_row[1] or 0), 6) if summary_row[1] is not None else 0.0,
        "total_runs": int(summary_row[2] or 0),
        "daily": [{"day": str(r[0]), "cost_usd": round(float(r[1]), 6)} for r in daily_rows],
    }
