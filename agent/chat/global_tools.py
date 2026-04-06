from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from agent.authz.policy import ChatPolicy
from agent.memory.config import build_postgres_dsn, load_memory_config


def _connect(dsn: str):
    import psycopg  # type: ignore[import-not-found]

    return psycopg.connect(dsn)


def _dsn() -> Optional[str]:
    cfg = load_memory_config()
    return build_postgres_dsn(cfg)


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    result: Any = None
    error: Optional[str] = None


def _norm_status(s: str) -> str:
    v = (s or "").strip().lower() or "all"
    if v not in ("open", "closed", "all"):
        return "all"
    return v


def _norm_key(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    v = str(s).strip()
    return v or None


def _norm_int(v: Any, *, default: int, lo: int, hi: int) -> int:
    try:
        x = int(v)
    except Exception:
        x = int(default)
    return max(lo, min(x, hi))


def _apply_scope_filters(policy: ChatPolicy, cte_conditions: List[str], params: List[Any]) -> None:
    # Best-effort: if policy restricts namespaces/clusters, limit DB aggregation to those.
    if policy.cluster_allowlist:
        cte_conditions.append("c.cluster = ANY(%s)")
        params.append(list(policy.cluster_allowlist))
    if policy.namespace_allowlist:
        cte_conditions.append("c.namespace = ANY(%s)")
        params.append(list(policy.namespace_allowlist))


def _cases_latest_runs_cte(
    *,
    policy: ChatPolicy,
    status: str,
    team: Optional[str],
    family: Optional[str],
    classification: Optional[str],
    since_hours: Optional[int],
) -> Tuple[str, List[Any]]:
    """
    Return (cte_where_sql, params) to filter the cases+runs CTE.
    """
    cond: List[str] = []
    params: List[Any] = []

    st = _norm_status(status)
    if st != "all":
        cond.append("c.status = %s")
        params.append(st)

    if since_hours is not None:
        h = _norm_int(since_hours, default=24, lo=1, hi=24 * 30)
        cond.append("c.updated_at >= (now() - (%s::int * interval '1 hour'))")
        params.append(h)

    if classification:
        cond.append("LOWER(COALESCE(r.classification, '')) = LOWER(%s)")
        params.append(classification)

    if family:
        cond.append("LOWER(COALESCE(r.family, '')) = LOWER(%s)")
        params.append(family)

    if team:
        cond.append("LOWER(NULLIF(r.analysis_json #>> '{target,team}', '')) = LOWER(%s)")
        params.append(team)

    _apply_scope_filters(policy, cond, params)

    where = " AND " + " AND ".join(cond) if cond else ""
    return where, params


def run_global_tool(*, policy: ChatPolicy, tool: str, args: Dict[str, Any]) -> ToolResult:
    """
    Global (Inbox/fleet) tools. These are intentionally narrow and read-only.
    """
    tool = (tool or "").strip()
    if not tool:
        return ToolResult(ok=False, error="tool_missing")

    dsn = _dsn()
    if not dsn:
        return ToolResult(ok=False, error="postgres_not_configured")

    try:
        return _run_global_tool_db(policy=policy, tool=tool, args=args, dsn=dsn)
    except Exception:
        return ToolResult(ok=False, error="db_unavailable")


def _run_global_tool_db(*, policy: ChatPolicy, tool: str, args: Dict[str, Any], dsn: str) -> ToolResult:
    # --------------------
    # cases.count
    # --------------------
    if tool == "cases.count":
        status = _norm_status(str(args.get("status") or "all"))
        team = _norm_key(args.get("team"))
        family = _norm_key(args.get("family"))
        classification = _norm_key(args.get("classification"))
        since_hours_raw = args.get("since_hours")
        since_hours = _norm_int(since_hours_raw, default=24, lo=1, hi=24 * 30) if since_hours_raw is not None else None

        cte_where, cte_params = _cases_latest_runs_cte(
            policy=policy,
            status=status,
            team=team,
            family=family,
            classification=classification,
            since_hours=since_hours,
        )

        with _connect(dsn) as conn:
            row = conn.execute(
                f"""
                WITH latest_runs AS (
                  SELECT DISTINCT ON (r.case_id)
                    r.case_id
                  FROM investigation_runs r
                  INNER JOIN cases c ON r.case_id = c.case_id
                  WHERE 1=1 {cte_where}
                  ORDER BY r.case_id, r.created_at DESC
                )
                SELECT COUNT(*)::int FROM latest_runs;
                """,
                tuple(cte_params),
            ).fetchone()
            n = int(row[0] or 0) if row else 0

        return ToolResult(
            ok=True,
            result={
                "status": status,
                "filters": {
                    "team": team,
                    "family": family,
                    "classification": classification,
                    "since_hours": since_hours,
                },
                "count": n,
            },
        )

    # --------------------
    # cases.top
    # --------------------
    if tool == "cases.top":
        by = str(args.get("by") or "").strip().lower() or "team"
        if by not in ("team", "family", "classification", "component"):
            return ToolResult(ok=False, error="by_invalid")
        limit = _norm_int(args.get("limit"), default=8, lo=1, hi=20)

        status = _norm_status(str(args.get("status") or "all"))
        since_hours_raw = args.get("since_hours")
        since_hours = _norm_int(since_hours_raw, default=24, lo=1, hi=24 * 30) if since_hours_raw is not None else None
        cte_where, cte_params = _cases_latest_runs_cte(
            policy=policy,
            status=status,
            team=None,
            family=None,
            classification=None,
            since_hours=since_hours,
        )

        # Map grouping field to a stable SQL expression.
        # Use direct columns for family/classification (indexed, always populated).
        # Team has no direct column; must use JSONB.
        if by == "team":
            field = "NULLIF(r.analysis_json #>> '{target,team}', '')"
        elif by == "family":
            field = "r.family"
        elif by == "component":
            field = "NULLIF(r.analysis_json #>> '{target,workload_name}', '')"
        else:
            field = "r.classification"

        with _connect(dsn) as conn:
            rows = conn.execute(
                f"""
                WITH latest_runs AS (
                  SELECT DISTINCT ON (r.case_id)
                    r.case_id,
                    {field} as key
                  FROM investigation_runs r
                  INNER JOIN cases c ON r.case_id = c.case_id
                  WHERE 1=1 {cte_where}
                  ORDER BY r.case_id, r.created_at DESC
                )
                SELECT
                  LOWER(COALESCE(key, 'unknown')) as key,
                  COUNT(*)::int as count
                FROM latest_runs
                GROUP BY LOWER(COALESCE(key, 'unknown'))
                ORDER BY count DESC, key ASC
                LIMIT %s;
                """,
                tuple(cte_params) + (limit,),
            ).fetchall()

        items = [{"key": str(r[0] or "unknown"), "count": int(r[1] or 0)} for r in rows or []]
        return ToolResult(ok=True, result={"by": by, "status": status, "since_hours": since_hours, "items": items})

    # --------------------
    # cases.lookup
    # --------------------
    if tool == "cases.lookup":
        ref = _norm_key(args.get("case_ref") or args.get("id") or args.get("case_id"))
        if not ref:
            return ToolResult(ok=False, error="case_ref_required")
        ref = ref.strip()
        # Allow "case_<prefix>" UI-style references.
        if ref.startswith("case_"):
            ref = ref[len("case_") :]
        ref = ref.strip().lower()

        with _connect(dsn) as conn:
            # Exact match first (uuid string).
            row = conn.execute(
                "SELECT case_id::text FROM cases WHERE case_id::text = %s LIMIT 1;",
                (ref,),
            ).fetchone()
            if row and row[0]:
                return ToolResult(ok=True, result={"matches": [str(row[0])], "mode": "exact"})

            # Prefix match (e.g., first 7 chars).
            like = f"{ref}%"
            rows = conn.execute(
                "SELECT case_id::text FROM cases WHERE LOWER(case_id::text) LIKE %s ORDER BY updated_at DESC LIMIT 5;",
                (like,),
            ).fetchall()
            matches = [str(r[0]) for r in (rows or []) if r and r[0]]
            return ToolResult(ok=True, result={"matches": matches, "mode": "prefix"})

    # --------------------
    # cases.summary
    # --------------------
    if tool == "cases.summary":
        ref = _norm_key(args.get("case_ref") or args.get("case_id"))
        if not ref:
            return ToolResult(ok=False, error="case_ref_required")
        # Reuse lookup logic.
        looked = run_global_tool(policy=policy, tool="cases.lookup", args={"case_ref": ref})
        if not looked.ok:
            return looked
        matches = (looked.result or {}).get("matches") if isinstance(looked.result, dict) else []
        if not matches:
            return ToolResult(ok=True, result={"found": False})
        cid = str(matches[0])

        with _connect(dsn) as conn:
            row = conn.execute(
                """
                WITH latest_run AS (
                  SELECT DISTINCT ON (r.case_id)
                    r.case_id,
                    r.run_id::text as run_id,
                    r.created_at::text as run_created_at,
                    r.alertname,
                    r.family,
                    r.classification,
                    NULLIF(r.analysis_json #>> '{target,team}', '') as team,
                    r.one_liner
                  FROM investigation_runs r
                  WHERE r.case_id::text = %s
                  ORDER BY r.case_id, r.created_at DESC
                )
                SELECT
                  c.case_id::text,
                  c.status,
                  c.created_at::text,
                  c.updated_at::text,
                  c.cluster,
                  c.namespace,
                  c.service,
                  lr.run_id,
                  lr.run_created_at,
                  lr.alertname,
                  lr.family,
                  lr.classification,
                  lr.team,
                  lr.one_liner
                FROM cases c
                LEFT JOIN latest_run lr ON lr.case_id = c.case_id
                WHERE c.case_id::text = %s
                LIMIT 1;
                """,
                (cid, cid),
            ).fetchone()
            if not row:
                return ToolResult(ok=True, result={"found": False})

        return ToolResult(
            ok=True,
            result={
                "found": True,
                "case": {
                    "case_id": str(row[0]),
                    "status": str(row[1] or ""),
                    "created_at": str(row[2] or ""),
                    "updated_at": str(row[3] or ""),
                    "cluster": (str(row[4]) if row[4] else None),
                    "namespace": (str(row[5]) if row[5] else None),
                    "service": (str(row[6]) if row[6] else None),
                },
                "latest_run": {
                    "run_id": (str(row[7]) if row[7] else None),
                    "created_at": (str(row[8]) if row[8] else None),
                    "alertname": (str(row[9]) if row[9] else None),
                    "family": (str(row[10]) if row[10] else None),
                    "classification": (str(row[11]) if row[11] else None),
                    "team": (str(row[12]) if row[12] else None),
                    "one_liner": (str(row[13]) if row[13] else None),
                },
            },
        )

    # --------------------
    # cases.search
    # --------------------
    if tool == "cases.search":
        q = _norm_key(args.get("q") or args.get("query"))
        if not q:
            return ToolResult(ok=False, error="q_required")
        limit = _norm_int(args.get("limit"), default=10, lo=1, hi=20)
        status = _norm_status(str(args.get("status") or "all"))

        # Build CTE filter
        cond: List[str] = []
        params: List[Any] = []

        if status != "all":
            cond.append("c.status = %s")
            params.append(status)

        _apply_scope_filters(policy, cond, params)

        # Tokenize q into words and match each against key fields (ILIKE AND across tokens)
        tokens = [t.strip().lower() for t in q.split() if t.strip()][:5]
        for tok in tokens:
            pattern = f"%{tok}%"
            cond.append(
                "(LOWER(r.alertname) LIKE %s"
                " OR LOWER(COALESCE(c.service, '')) LIKE %s"
                " OR LOWER(COALESCE(r.namespace, '')) LIKE %s"
                " OR LOWER(COALESCE(r.cluster, '')) LIKE %s"
                " OR LOWER(COALESCE(r.analysis_json #>> '{analysis,verdict,one_liner}', '')) LIKE %s)"
            )
            params.extend([pattern, pattern, pattern, pattern, pattern])

        where = " AND " + " AND ".join(cond) if cond else ""

        with _connect(dsn) as conn:
            rows = conn.execute(
                f"""
                WITH latest_runs AS (
                  SELECT DISTINCT ON (r.case_id)
                    r.case_id,
                    r.alertname,
                    r.namespace,
                    r.cluster,
                    r.family,
                    r.classification,
                    NULLIF(r.analysis_json #>> '{{analysis,verdict,one_liner}}', '') as one_liner,
                    NULLIF(r.analysis_json #>> '{{target,team}}', '') as team
                  FROM investigation_runs r
                  INNER JOIN cases c ON r.case_id = c.case_id
                  WHERE 1=1 {where}
                  ORDER BY r.case_id, r.created_at DESC
                )
                SELECT
                  c.case_id::text,
                  c.status,
                  c.updated_at::text,
                  c.service,
                  c.snoozed_until::text,
                  lr.alertname,
                  lr.namespace,
                  lr.cluster,
                  lr.family,
                  lr.classification,
                  lr.one_liner,
                  lr.team
                FROM cases c
                INNER JOIN latest_runs lr ON c.case_id = lr.case_id
                ORDER BY c.updated_at DESC
                LIMIT %s;
                """,
                tuple(params) + (limit,),
            ).fetchall()

        items = []
        for r in rows or []:
            items.append(
                {
                    "case_id": str(r[0]),
                    "status": str(r[1] or ""),
                    "updated_at": str(r[2] or ""),
                    "service": (str(r[3]) if r[3] else None),
                    "snoozed_until": (str(r[4]) if r[4] else None),
                    "alertname": (str(r[5]) if r[5] else None),
                    "namespace": (str(r[6]) if r[6] else None),
                    "cluster": (str(r[7]) if r[7] else None),
                    "family": (str(r[8]) if r[8] else None),
                    "classification": (str(r[9]) if r[9] else None),
                    "one_liner": (str(r[10]) if r[10] else None),
                    "team": (str(r[11]) if r[11] else None),
                }
            )

        return ToolResult(ok=True, result={"q": q, "status": status, "count": len(items), "cases": items})

    # --------------------
    # cases.status_breakdown
    # --------------------
    if tool == "cases.status_breakdown":
        since_hours_raw = args.get("since_hours")
        since_hours = _norm_int(since_hours_raw, default=24, lo=1, hi=24 * 30) if since_hours_raw is not None else None
        stale_hours = _norm_int(args.get("stale_hours"), default=24, lo=1, hi=24 * 7)

        cond: List[str] = []
        params: List[Any] = []

        if since_hours is not None:
            cond.append("c.updated_at >= (now() - (%s::int * interval '1 hour'))")
            params.append(since_hours)

        _apply_scope_filters(policy, cond, params)
        where = " AND " + " AND ".join(cond) if cond else ""

        with _connect(dsn) as conn:
            row = conn.execute(
                f"""
                SELECT
                  COUNT(*) FILTER (
                    WHERE c.status = 'open'
                      AND (c.snoozed_until IS NULL OR c.snoozed_until <= now())
                      AND c.updated_at >= now() - (%s::int * interval '1 hour')
                  )::int AS firing,
                  COUNT(*) FILTER (
                    WHERE c.snoozed_until > now()
                  )::int AS snoozed,
                  COUNT(*) FILTER (
                    WHERE c.status = 'open'
                      AND (c.snoozed_until IS NULL OR c.snoozed_until <= now())
                      AND c.updated_at < now() - (%s::int * interval '1 hour')
                  )::int AS stale,
                  COUNT(*) FILTER (
                    WHERE c.status = 'closed'
                      AND (c.snoozed_until IS NULL OR c.snoozed_until <= now())
                  )::int AS resolved
                FROM cases c
                WHERE 1=1 {where};
                """,
                (stale_hours, stale_hours) + tuple(params),
            ).fetchone()

        firing = int(row[0] or 0) if row else 0
        snoozed = int(row[1] or 0) if row else 0
        stale = int(row[2] or 0) if row else 0
        resolved = int(row[3] or 0) if row else 0

        return ToolResult(
            ok=True,
            result={
                "breakdown": {
                    "firing": firing,
                    "snoozed": snoozed,
                    "stale": stale,
                    "resolved": resolved,
                    "total": firing + snoozed + stale + resolved,
                },
                "filters": {
                    "since_hours": since_hours,
                    "stale_hours": stale_hours,
                },
            },
        )

    # --------------------
    # cases.recent
    # --------------------
    if tool == "cases.recent":
        limit = _norm_int(args.get("limit"), default=10, lo=1, hi=20)
        status = _norm_status(str(args.get("status") or "open"))
        since_hours_raw = args.get("since_hours")
        since_hours = _norm_int(since_hours_raw, default=24, lo=1, hi=24 * 30) if since_hours_raw is not None else None

        cond: List[str] = []
        params: List[Any] = []

        if status != "all":
            cond.append("c.status = %s")
            params.append(status)

        if since_hours is not None:
            cond.append("c.updated_at >= (now() - (%s::int * interval '1 hour'))")
            params.append(since_hours)

        _apply_scope_filters(policy, cond, params)
        where = " AND " + " AND ".join(cond) if cond else ""

        with _connect(dsn) as conn:
            rows = conn.execute(
                f"""
                WITH latest_runs AS (
                  SELECT DISTINCT ON (r.case_id)
                    r.case_id,
                    r.alertname,
                    r.family,
                    r.classification,
                    r.severity,
                    NULLIF(r.analysis_json #>> '{{analysis,verdict,one_liner}}', '') as one_liner,
                    NULLIF(r.analysis_json #>> '{{target,team}}', '') as team
                  FROM investigation_runs r
                  INNER JOIN cases c ON r.case_id = c.case_id
                  WHERE 1=1 {where}
                  ORDER BY r.case_id, r.created_at DESC
                )
                SELECT
                  c.case_id::text,
                  c.status,
                  c.updated_at::text,
                  c.service,
                  c.namespace,
                  c.snoozed_until::text,
                  lr.alertname,
                  lr.family,
                  lr.classification,
                  lr.severity,
                  lr.one_liner,
                  lr.team
                FROM cases c
                INNER JOIN latest_runs lr ON c.case_id = lr.case_id
                ORDER BY c.updated_at DESC
                LIMIT %s;
                """,
                tuple(params) + (limit,),
            ).fetchall()

        items = []
        for r in rows or []:
            snoozed_until = str(r[5]) if r[5] else None
            status_val = str(r[1] or "")
            if snoozed_until:
                effective = "snoozed"
            elif status_val == "closed":
                effective = "resolved"
            else:
                effective = "firing"
            items.append(
                {
                    "case_id": str(r[0]),
                    "effective_status": effective,
                    "updated_at": str(r[2] or ""),
                    "service": (str(r[3]) if r[3] else None),
                    "namespace": (str(r[4]) if r[4] else None),
                    "alertname": (str(r[6]) if r[6] else None),
                    "family": (str(r[7]) if r[7] else None),
                    "classification": (str(r[8]) if r[8] else None),
                    "severity": (str(r[9]) if r[9] else None),
                    "one_liner": (str(r[10]) if r[10] else None),
                    "team": (str(r[11]) if r[11] else None),
                }
            )

        return ToolResult(ok=True, result={"status": status, "count": len(items), "cases": items})

    # --------------------
    # cases.by_severity
    # --------------------
    if tool == "cases.by_severity":
        status = _norm_status(str(args.get("status") or "open"))
        since_hours_raw = args.get("since_hours")
        since_hours = _norm_int(since_hours_raw, default=24, lo=1, hi=24 * 30) if since_hours_raw is not None else None

        cond: List[str] = []
        params: List[Any] = []

        if status != "all":
            cond.append("c.status = %s")
            params.append(status)

        if since_hours is not None:
            cond.append("c.updated_at >= (now() - (%s::int * interval '1 hour'))")
            params.append(since_hours)

        _apply_scope_filters(policy, cond, params)
        where = " AND " + " AND ".join(cond) if cond else ""

        with _connect(dsn) as conn:
            rows = conn.execute(
                f"""
                WITH latest_runs AS (
                  SELECT DISTINCT ON (r.case_id)
                    r.case_id,
                    LOWER(COALESCE(r.severity, 'unknown')) as severity
                  FROM investigation_runs r
                  INNER JOIN cases c ON r.case_id = c.case_id
                  WHERE 1=1 {where}
                  ORDER BY r.case_id, r.created_at DESC
                )
                SELECT severity, COUNT(*)::int as count
                FROM latest_runs
                GROUP BY severity
                ORDER BY count DESC;
                """,
                tuple(params),
            ).fetchall()

        known = {"critical": 0, "warning": 0, "info": 0, "unknown": 0}
        for sev, cnt in rows or []:
            key = str(sev or "unknown").strip().lower()
            if key in known:
                known[key] = int(cnt or 0)
            else:
                known["unknown"] = known.get("unknown", 0) + int(cnt or 0)

        return ToolResult(
            ok=True,
            result={
                "status": status,
                "since_hours": since_hours,
                "breakdown": known,
                "total": sum(known.values()),
            },
        )

    # --------------------
    # cases.trending
    # --------------------
    if tool == "cases.trending":
        window_hours = _norm_int(args.get("window_hours"), default=24, lo=1, hi=24 * 7)
        by = str(args.get("by") or "family").strip().lower()
        if by not in ("family", "team", "classification"):
            return ToolResult(ok=False, error="by_invalid: must be family, team, or classification")
        limit = _norm_int(args.get("limit"), default=10, lo=1, hi=20)

        if by == "team":
            field = "NULLIF(r.analysis_json #>> '{target,team}', '')"
        elif by == "classification":
            field = "r.classification"
        else:
            field = "r.family"

        cond: List[str] = []
        params: List[Any] = []
        _apply_scope_filters(policy, cond, params)
        where = " AND " + " AND ".join(cond) if cond else ""

        with _connect(dsn) as conn:
            rows = conn.execute(
                f"""
                WITH bucketed AS (
                  SELECT
                    LOWER(COALESCE({field}, 'unknown')) as key,
                    COUNT(*) FILTER (
                      WHERE r.created_at >= now() - (%s::int * interval '1 hour')
                    )::int AS current_count,
                    COUNT(*) FILTER (
                      WHERE r.created_at >= now() - (%s::int * interval '1 hour') * 2
                        AND r.created_at < now() - (%s::int * interval '1 hour')
                    )::int AS previous_count
                  FROM investigation_runs r
                  INNER JOIN cases c ON r.case_id = c.case_id
                  WHERE r.created_at >= now() - (%s::int * interval '1 hour') * 2
                    {where}
                  GROUP BY key
                )
                SELECT key, current_count, previous_count,
                       (current_count - previous_count) as delta
                FROM bucketed
                WHERE current_count > 0 OR previous_count > 0
                ORDER BY current_count DESC, delta DESC
                LIMIT %s;
                """,
                (window_hours, window_hours, window_hours, window_hours) + tuple(params) + (limit,),
            ).fetchall()

        items = [
            {
                "key": str(r[0] or "unknown"),
                "current": int(r[1] or 0),
                "previous": int(r[2] or 0),
                "delta": int(r[3] or 0),
            }
            for r in rows or []
        ]

        return ToolResult(
            ok=True,
            result={
                "by": by,
                "window_hours": window_hours,
                "items": items,
            },
        )

    # --------------------
    # exec.overview
    # --------------------
    if tool == "exec.overview":
        if not policy.allow_exec_read:
            return ToolResult(ok=False, error="tool_not_allowed")
        days = max(1, min(int(args.get("days") or 30), 90))
        from agent.memory.console_queries import get_exec_overview

        with _connect(dsn) as conn:
            data = get_exec_overview(conn, days=days)

        signal = data.get("signal", {})
        savings = data.get("savings", {})
        risk = data.get("risk", {})
        ai = data.get("ai", {})
        return ToolResult(
            ok=True,
            result={
                "window_days": days,
                "signal": {
                    "total_runs": signal.get("total_runs"),
                    "actionable_pct": signal.get("actionable_pct"),
                    "noisy": signal.get("noisy"),
                    "change_correlated_count": signal.get("change_correlated_count"),
                },
                "savings": {
                    "deflected_runs": savings.get("deflected_runs"),
                    "hours_saved": savings.get("hours_saved"),
                    "cost_saved_usd": savings.get("cost_saved_usd"),
                },
                "risk": {
                    "active_count": risk.get("active_count"),
                    "active_high_impact_count": risk.get("active_high_impact_count"),
                    "stale_investigation_count": risk.get("stale_investigation_count"),
                    "critical_this_month": risk.get("critical_this_month"),
                    "total_this_month": risk.get("total_this_month"),
                    "top_active": risk.get("top_active", [])[:3],
                },
                "ai": ai,
                "recurrence_rate": data.get("recurrence", {}).get("rate"),
                "top_services": data.get("focus", {}).get("top_services", [])[:3],
                "top_teams": data.get("focus", {}).get("top_teams", [])[:3],
            },
        )

    return ToolResult(ok=False, error="unknown_tool")
