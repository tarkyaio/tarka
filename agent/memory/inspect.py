from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List, Optional, Tuple

from agent.memory.config import build_postgres_dsn, load_memory_config


def _connect(dsn: str):
    import psycopg  # type: ignore[import-not-found]

    return psycopg.connect(dsn)


def get_memory_stats() -> Tuple[bool, str, Dict[str, Any]]:
    cfg = load_memory_config()
    dsn = build_postgres_dsn(cfg)
    if not dsn:
        return False, "Postgres not configured", {"memory_enabled": cfg.memory_enabled}

    with _connect(dsn) as conn:
        cases = conn.execute("SELECT COUNT(*) FROM cases;").fetchone()[0]
        runs = conn.execute("SELECT COUNT(*) FROM investigation_runs;").fetchone()[0]
        skills = conn.execute("SELECT COUNT(*) FROM skills;").fetchone()[0]
        feedback = conn.execute("SELECT COUNT(*) FROM skill_feedback;").fetchone()[0]
        cases_missing_cluster = conn.execute("SELECT COUNT(*) FROM cases WHERE cluster IS NULL;").fetchone()[0]
        pod_cases_missing_workload = conn.execute(
            "SELECT COUNT(*) FROM cases WHERE target_type = 'pod' AND workload_kind IS NULL;"
        ).fetchone()[0]
        cases_missing_s3 = conn.execute("SELECT COUNT(*) FROM cases WHERE s3_report_key IS NULL;").fetchone()[0]
        cases_multi_s3 = conn.execute("""
            SELECT COUNT(*) FROM (
              SELECT case_id
              FROM investigation_runs
              GROUP BY case_id
              HAVING COUNT(DISTINCT s3_report_key) > 1
            ) t;
            """).fetchone()[0]
        latest = conn.execute("""
            SELECT run_id::text, case_id::text, created_at::text, alertname, family, one_liner, s3_report_key
            FROM investigation_runs
            ORDER BY created_at DESC
            LIMIT 5;
            """).fetchall()

    latest_runs: List[Dict[str, Any]] = []
    for r in latest or []:
        latest_runs.append(
            {
                "run_id": r[0],
                "case_id": r[1],
                "created_at": r[2],
                "alertname": r[3],
                "family": r[4],
                "one_liner": r[5],
                "s3_report_key": r[6],
            }
        )

    return (
        True,
        "ok",
        {
            "memory_enabled": cfg.memory_enabled,
            "postgres_configured": True,
            "counts": {
                "cases": int(cases),
                "investigation_runs": int(runs),
                "skills": int(skills),
                "skill_feedback": int(feedback),
            },
            "integrity": {
                "cases_missing_cluster": int(cases_missing_cluster),
                "pod_cases_missing_workload": int(pod_cases_missing_workload),
                "cases_missing_s3_report_key": int(cases_missing_s3),
                "cases_with_multiple_s3_report_keys_in_runs": int(cases_multi_s3),
            },
            "latest_runs": latest_runs,
        },
    )


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Inspect memory store (Postgres) state.")
    _ = p.parse_args(argv)
    ok, msg, payload = get_memory_stats()
    payload["status"] = msg
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
