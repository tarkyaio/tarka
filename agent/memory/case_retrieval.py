from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from agent.core.models import Investigation
from agent.memory.config import build_postgres_dsn, load_memory_config


@dataclass(frozen=True)
class SimilarRun:
    case_id: str
    run_id: str
    created_at: str
    one_liner: str
    s3_report_key: Optional[str] = None
    resolution_category: Optional[str] = None
    resolution_summary: Optional[str] = None
    postmortem_link: Optional[str] = None


def _connect(dsn: str):
    import psycopg  # type: ignore[import-not-found]

    return psycopg.connect(dsn)


def _extract_job_prefix(job_name: str) -> Optional[str]:
    """
    Extract a stable prefix from generated Job names.

    Many Jobs are created by CronJobs or other generators with patterns like:
    - batch-etl-job-57821-0-fywpu (CronJob with attempt + random)
    - my-job-1234567890-abcde (timestamp + random)
    - job-blah-1, job-blah-2 (simple sequential)

    We extract the prefix before the generated suffix to match similar jobs.
    """
    import re

    # Pattern 1: job-name-{number}-{number}-{random}
    # Example: batch-etl-job-57821-0-fywpu
    match = re.match(r"^(.+?)(?:-\d+){1,2}-[a-z0-9]{5,}$", job_name)
    if match:
        return match.group(1)

    # Pattern 2: job-name-{number}-{random} (single number + random suffix)
    # Example: my-cronjob-1234567890-abcde
    match = re.match(r"^(.+?)-\d+-[a-z0-9]{5,}$", job_name)
    if match:
        return match.group(1)

    # Pattern 3: job-name-{number} (simple sequential, no random suffix)
    # Example: job-blah-1, job-blah-2, my-job-123
    # Only match if it ends with a number (not just contains a number mid-name)
    match = re.match(r"^(.+)-\d+$", job_name)
    if match:
        prefix = match.group(1)
        # Avoid false positives: ensure the prefix is at least 3 chars
        # This prevents matching "a-1", "x-2" which might be legitimate job names
        if len(prefix) >= 3:
            return prefix

    return None


def find_similar_runs(investigation: Investigation, *, limit: int = 5) -> Tuple[bool, str, List[SimilarRun]]:
    """
    Simple similarity for now: filter by family + cluster + (namespace/workload when present).

    For Jobs with generated names (e.g., CronJob patterns), uses prefix matching instead of
    exact name matching to group similar job failures together.
    """
    cfg = load_memory_config()
    dsn = build_postgres_dsn(cfg)
    if not dsn:
        return False, "Postgres not configured", []

    features = investigation.analysis.features
    family = getattr(features, "family", None) if features is not None else None
    family = str(family) if family else None
    if not family:
        return True, "no family", []

    cluster = (investigation.target.cluster or "").strip() or None
    namespace = (investigation.target.namespace or "").strip() or None
    workload_name = (investigation.target.workload_name or "").strip() or None
    workload_kind = (investigation.target.workload_kind or "").strip() or None
    current_fp = (investigation.alert.fingerprint or "").strip() or None

    # IMPORTANT: Build optional filters dynamically.
    # Using patterns like `(%s IS NULL OR col IS NOT DISTINCT FROM %s)` can trigger
    # `IndeterminateDatatype` when psycopg sends NULL without a concrete type.
    where = ["r.family = %s"]
    params: List[object] = [family]
    if cluster is not None:
        where.append("r.cluster IS NOT DISTINCT FROM %s")
        params.append(cluster)
    if namespace is not None:
        where.append("r.namespace IS NOT DISTINCT FROM %s")
        params.append(namespace)

    # For Jobs: try prefix matching for generated job names (CronJob pattern)
    job_prefix = None
    if workload_kind == "Job" and workload_name is not None:
        job_prefix = _extract_job_prefix(workload_name)

    if job_prefix is not None:
        # Use prefix matching with LIKE for CronJob-style generated names
        where.append("r.workload_name LIKE %s")
        params.append(f"{job_prefix}%")
    elif workload_name is not None:
        # Exact match for non-generated job names
        where.append("r.workload_name IS NOT DISTINCT FROM %s")
        params.append(workload_name)

    if workload_kind is not None:
        where.append("r.workload_kind IS NOT DISTINCT FROM %s")
        params.append(workload_kind)
    if current_fp is not None:
        # Avoid returning the current run/fingerprint.
        where.append("r.alert_fingerprint IS DISTINCT FROM %s")
        params.append(current_fp)

    sql = (
        """
        SELECT
          r.case_id,
          r.run_id,
          r.created_at::text,
          COALESCE(r.one_liner, ''),
          r.s3_report_key,
          c.resolution_category,
          c.resolution_summary,
          c.postmortem_link
        FROM investigation_runs r
        INNER JOIN cases c ON r.case_id = c.case_id
        WHERE """
        + " AND ".join(where)
        + """
        ORDER BY r.created_at DESC
        LIMIT %s;
        """
    )
    params.append(int(limit))

    with _connect(dsn) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()

    out: List[SimilarRun] = []
    for r in rows or []:
        out.append(
            SimilarRun(
                case_id=str(r[0]),
                run_id=str(r[1]),
                created_at=str(r[2]),
                one_liner=str(r[3] or ""),
                s3_report_key=str(r[4]) if r[4] else None,
                resolution_category=str(r[5]) if r[5] else None,
                resolution_summary=str(r[6]) if r[6] else None,
                postmortem_link=str(r[7]) if r[7] else None,
            )
        )
    return True, "ok", out
