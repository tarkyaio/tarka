from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class CaseizeInput:
    alert_fingerprint: Optional[str]
    alertname: Optional[str]
    family: Optional[str]

    cluster: Optional[str]
    target_type: Optional[str]
    namespace: Optional[str]
    container: Optional[str]
    workload_kind: Optional[str]
    workload_name: Optional[str]
    service: Optional[str]
    instance: Optional[str]


def _case_key_for_fingerprint(fp: str) -> str:
    return f"fp:{fp}"


def _case_key_for_workload(inp: CaseizeInput) -> Optional[str]:
    """
    Stable case key for rollout-noisy alerts keyed by workload identity.

    We intentionally include alertname+family so distinct alerts on the same workload don't collide.
    For `KubernetesContainerOomKiller` we include container so different containers in the same workload
    become distinct cases.
    """
    if not (inp.cluster and inp.namespace and inp.workload_kind and inp.workload_name and inp.alertname and inp.family):
        return None
    if inp.alertname not in (
        "KubernetesPodNotHealthy",
        "KubernetesPodNotHealthyCritical",
        "KubernetesContainerOomKiller",
        "KubeJobFailed",
    ):
        return None
    payload = {
        "k": "workload",
        "cluster": inp.cluster,
        "namespace": inp.namespace,
        "workload_kind": inp.workload_kind,
        "workload_name": inp.workload_name,
        "family": inp.family,
        "alertname": inp.alertname,
        "container": (inp.container if inp.alertname == "KubernetesContainerOomKiller" else None),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "wl:" + hashlib.sha256(raw).hexdigest()


def _case_key_for_group(inp: CaseizeInput, *, day_bucket: str) -> str:
    """
    A stable key to avoid duplicate case rows under concurrency.

    We intentionally bucket by UTC day for now; later we can evolve this without breaking schema.
    """
    payload = {
        "k": "group_day",
        "day": day_bucket,
        "cluster": inp.cluster,
        "target_type": inp.target_type,
        "namespace": inp.namespace,
        "workload_kind": inp.workload_kind,
        "workload_name": inp.workload_name,
        "service": inp.service,
        "instance": inp.instance,
        "family": inp.family,
        "alertname": inp.alertname,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "g:" + hashlib.sha256(raw).hexdigest()


def caseize_run(conn, inp: CaseizeInput) -> Tuple[str, str, bool]:
    """
    Map a run to a case_id using deterministic rules.

    Returns: (case_id, reason, created_new)
    """
    # Prefer workload identity for specific rollout-noisy alertnames (ignore fingerprint churn).
    wl_key = _case_key_for_workload(inp)
    if wl_key:
        row = conn.execute(
            """
            INSERT INTO cases(
              case_key,
              status,
              cluster, target_type, namespace,
              workload_kind, workload_name,
              service, instance,
              family
            )
            VALUES (
              %s,
              'open',
              %s, %s, %s,
              %s, %s,
              %s, %s,
              %s
            )
            ON CONFLICT (case_key) DO UPDATE
              SET updated_at = now(),
                  cluster = COALESCE(cases.cluster, EXCLUDED.cluster),
                  target_type = COALESCE(cases.target_type, EXCLUDED.target_type),
                  namespace = COALESCE(cases.namespace, EXCLUDED.namespace),
                  workload_kind = COALESCE(cases.workload_kind, EXCLUDED.workload_kind),
                  workload_name = COALESCE(cases.workload_name, EXCLUDED.workload_name),
                  service = COALESCE(cases.service, EXCLUDED.service),
                  instance = COALESCE(cases.instance, EXCLUDED.instance),
                  family = COALESCE(cases.family, EXCLUDED.family)
            RETURNING case_id;
            """,
            (
                wl_key,
                inp.cluster,
                inp.target_type,
                inp.namespace,
                inp.workload_kind,
                inp.workload_name,
                inp.service,
                inp.instance,
                inp.family,
            ),
        ).fetchone()
        if not row or not row[0]:
            raise RuntimeError("Failed to create/reuse case for workload identity")
        return str(row[0]), "workload_upsert", True

    fp = (inp.alert_fingerprint or "").strip() or None
    if fp:
        # First: if we already indexed this fingerprint, reuse it.
        row = conn.execute(
            "SELECT case_id FROM investigation_runs WHERE alert_fingerprint = %s ORDER BY created_at DESC LIMIT 1;",
            (fp,),
        ).fetchone()
        if row and row[0]:
            return str(row[0]), "fingerprint_existing_run", False

        # Otherwise: create/reuse the case row via a unique case_key (concurrency-safe).
        ck = _case_key_for_fingerprint(fp)
        row = conn.execute(
            """
            INSERT INTO cases(
              case_key,
              status,
              cluster, target_type, namespace,
              workload_kind, workload_name,
              service, instance,
              family
            )
            VALUES (
              %s,
              'open',
              %s, %s, %s,
              %s, %s,
              %s, %s,
              %s
            )
            ON CONFLICT (case_key) DO UPDATE
              SET updated_at = now(),
                  cluster = COALESCE(cases.cluster, EXCLUDED.cluster),
                  target_type = COALESCE(cases.target_type, EXCLUDED.target_type),
                  namespace = COALESCE(cases.namespace, EXCLUDED.namespace),
                  workload_kind = COALESCE(cases.workload_kind, EXCLUDED.workload_kind),
                  workload_name = COALESCE(cases.workload_name, EXCLUDED.workload_name),
                  service = COALESCE(cases.service, EXCLUDED.service),
                  instance = COALESCE(cases.instance, EXCLUDED.instance),
                  family = COALESCE(cases.family, EXCLUDED.family)
            RETURNING case_id;
            """,
            (
                ck,
                inp.cluster,
                inp.target_type,
                inp.namespace,
                inp.workload_kind,
                inp.workload_name,
                inp.service,
                inp.instance,
                inp.family,
            ),
        ).fetchone()
        if not row or not row[0]:
            raise RuntimeError("Failed to create/reuse case for fingerprint")
        return str(row[0]), "fingerprint_upsert", True

    # Best-effort grouping match: look for a recent open case with a similar prior run.
    row = conn.execute(
        """
        SELECT c.case_id
        FROM investigation_runs r
        JOIN cases c ON c.case_id = r.case_id
        WHERE c.status = 'open'
          AND c.updated_at >= (now() - interval '24 hours')
          AND r.cluster IS NOT DISTINCT FROM %s
          AND r.target_type IS NOT DISTINCT FROM %s
          AND r.namespace IS NOT DISTINCT FROM %s
          AND r.workload_kind IS NOT DISTINCT FROM %s
          AND r.workload_name IS NOT DISTINCT FROM %s
          AND r.service IS NOT DISTINCT FROM %s
          AND r.instance IS NOT DISTINCT FROM %s
          AND r.family IS NOT DISTINCT FROM %s
          AND r.alertname IS NOT DISTINCT FROM %s
        ORDER BY c.updated_at DESC
        LIMIT 1;
        """,
        (
            inp.cluster,
            inp.target_type,
            inp.namespace,
            inp.workload_kind,
            inp.workload_name,
            inp.service,
            inp.instance,
            inp.family,
            inp.alertname,
        ),
    ).fetchone()
    if row and row[0]:
        return str(row[0]), "group_24h", False

    # Create/reuse a grouped case row via case_key (avoid duplicates under concurrent webhooks).
    # Bucket by UTC day (stable string from Postgres) so the key doesn't change mid-day.
    day_bucket = conn.execute("SELECT to_char(date_trunc('day', now() AT TIME ZONE 'UTC'), 'YYYY-MM-DD');").fetchone()[
        0
    ]
    ck = _case_key_for_group(inp, day_bucket=f"utc:{day_bucket}")
    row = conn.execute(
        """
        INSERT INTO cases(
          case_key,
          status,
          cluster, target_type, namespace,
          workload_kind, workload_name,
          service, instance,
          family
        )
        VALUES (
          %s,
          'open',
          %s, %s, %s,
          %s, %s,
          %s, %s,
          %s
        )
        ON CONFLICT (case_key) DO UPDATE
          SET updated_at = now()
        RETURNING case_id;
        """,
        (
            ck,
            inp.cluster,
            inp.target_type,
            inp.namespace,
            inp.workload_kind,
            inp.workload_name,
            inp.service,
            inp.instance,
            inp.family,
        ),
    ).fetchone()
    if not row or not row[0]:
        raise RuntimeError("Failed to create/reuse grouped case")
    return str(row[0]), "group_upsert", True
