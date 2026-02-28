from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from agent.core.models import Investigation
from agent.dump import investigation_to_json_dict
from agent.memory.config import build_postgres_dsn, load_memory_config
from agent.memory.incidentize import IncidentizeInput, incidentize_run


@dataclass(frozen=True)
class IndexResult:
    incident_id: str
    run_id: str
    incident_match_reason: str


def _connect(dsn: str):
    import psycopg  # type: ignore[import-not-found]

    return psycopg.connect(dsn)


def _safe_str(x: Any) -> Optional[str]:
    if x is None:
        return None
    s = str(x).strip()
    return s or None


def index_incident_run(
    *,
    investigation: Investigation,
    s3_report_key: Optional[str] = None,
    s3_investigation_key: Optional[str] = None,
    report_text: Optional[str] = None,
) -> Tuple[bool, str, Optional[IndexResult]]:
    """
    Persist an incident run index row in Postgres.

    This is best-effort and should be called from the webhook after S3 writes.
    """
    cfg = load_memory_config()
    dsn = build_postgres_dsn(cfg)
    if not dsn:
        return False, "Postgres not configured", None

    labels = investigation.alert.labels if isinstance(investigation.alert.labels, dict) else {}
    alertname = _safe_str(labels.get("alertname"))
    severity = _safe_str(labels.get("severity"))

    features = investigation.analysis.features
    verdict = investigation.analysis.verdict
    scores = investigation.analysis.scores

    family = _safe_str(getattr(features, "family", None) if features is not None else None)
    classification = _safe_str(getattr(verdict, "classification", None) if verdict is not None else None)
    primary_driver = _safe_str(getattr(verdict, "primary_driver", None) if verdict is not None else None)
    one_liner = _safe_str(getattr(verdict, "one_liner", None) if verdict is not None else None)
    reason_codes = list(getattr(scores, "reason_codes", []) or []) if scores is not None else []

    # Compact analysis payload (stable, explainable)
    analysis_json: Dict[str, Any] = investigation_to_json_dict(investigation, mode="analysis")
    analysis_json_s = json.dumps(analysis_json, ensure_ascii=False)

    inp = IncidentizeInput(
        alert_fingerprint=_safe_str(investigation.alert.fingerprint),
        alertname=alertname,
        family=family,
        cluster=_safe_str(investigation.target.cluster),
        target_type=_safe_str(investigation.target.target_type),
        namespace=_safe_str(investigation.target.namespace),
        workload_kind=_safe_str(investigation.target.workload_kind),
        workload_name=_safe_str(investigation.target.workload_name),
        service=_safe_str(investigation.target.service),
        instance=_safe_str(investigation.target.instance),
    )

    with _connect(dsn) as conn:
        with conn.transaction():
            incident_id, reason, _created_new = incidentize_run(conn, inp)

            row = conn.execute(
                """
                INSERT INTO incident_runs(
                  incident_id,
                  alert_fingerprint, alertname, severity, starts_at, normalized_state,
                  target_type, cluster, namespace, pod, container, workload_kind, workload_name, service, instance,
                  family, classification, primary_driver, one_liner, reason_codes,
                  s3_report_key, s3_investigation_key,
                  analysis_json, report_text,
                  incident_match_reason
                )
                VALUES (
                  %s,
                  %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s,
                  %s, %s,
                  %s::jsonb, %s,
                  %s
                )
                RETURNING run_id;
                """,
                (
                    incident_id,
                    _safe_str(investigation.alert.fingerprint),
                    alertname,
                    severity,
                    _safe_str(investigation.alert.starts_at),
                    _safe_str(investigation.alert.normalized_state),
                    _safe_str(investigation.target.target_type),
                    _safe_str(investigation.target.cluster),
                    _safe_str(investigation.target.namespace),
                    _safe_str(investigation.target.pod),
                    _safe_str(investigation.target.container),
                    _safe_str(investigation.target.workload_kind),
                    _safe_str(investigation.target.workload_name),
                    _safe_str(investigation.target.service),
                    _safe_str(investigation.target.instance),
                    family,
                    classification,
                    primary_driver,
                    one_liner,
                    reason_codes if reason_codes else None,
                    _safe_str(s3_report_key),
                    _safe_str(s3_investigation_key),
                    analysis_json_s,
                    report_text,
                    reason,
                ),
            ).fetchone()

            run_id = str(row[0]) if row and row[0] else ""
            # Update incident summary for faster browsing
            conn.execute(
                """
                UPDATE incidents
                SET updated_at = now(),
                    family = COALESCE(%s, family),
                    primary_driver = COALESCE(%s, primary_driver),
                    latest_one_liner = COALESCE(%s, latest_one_liner),
                    s3_report_key = COALESCE(%s, s3_report_key),
                    s3_investigation_key = COALESCE(%s, s3_investigation_key),
                    cluster = COALESCE(cluster, %s),
                    target_type = COALESCE(target_type, %s),
                    namespace = COALESCE(namespace, %s),
                    workload_kind = COALESCE(workload_kind, %s),
                    workload_name = COALESCE(workload_name, %s),
                    service = COALESCE(service, %s),
                    instance = COALESCE(instance, %s)
                WHERE incident_id = %s;
                """,
                (
                    family,
                    primary_driver,
                    one_liner,
                    _safe_str(s3_report_key),
                    _safe_str(s3_investigation_key),
                    _safe_str(investigation.target.cluster),
                    _safe_str(investigation.target.target_type),
                    _safe_str(investigation.target.namespace),
                    _safe_str(investigation.target.workload_kind),
                    _safe_str(investigation.target.workload_name),
                    _safe_str(investigation.target.service),
                    _safe_str(investigation.target.instance),
                    incident_id,
                ),
            )

    return True, "indexed", IndexResult(incident_id=incident_id, run_id=run_id, incident_match_reason=reason)
