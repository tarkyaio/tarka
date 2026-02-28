"""Investigation → evidence pack for LLM enrichment.

This module is deterministic and has NO provider SDK imports.
It should be safe to use in local runs even when no API keys exist.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from agent.core.models import Investigation


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "y", "on")


def _truncate(s: Any, n: int) -> str:
    txt = str(s) if s is not None else ""
    if len(txt) <= n:
        return txt
    return txt[: max(0, n - 1)] + "…"


def _to_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def _series_max(series: List[Dict[str, Any]]) -> float:
    m = 0.0
    for s in series or []:
        for _, v in s.get("values") or []:
            fv = _to_float(v)
            if fv is not None and fv > m:
                m = fv
    return m


def build_evidence_pack(investigation: Investigation) -> Dict[str, Any]:
    """
    Build a compact JSON evidence object for the LLM.

    By default this includes logs metadata only (not raw logs). Set `LLM_INCLUDE_LOGS=1` to include a small tail sample.
    """
    include_logs = _env_bool("LLM_INCLUDE_LOGS", False)

    labels = investigation.alert.labels or {}
    annotations = investigation.alert.annotations or {}

    # Logs metadata only by default (avoid sending raw logs initially).
    logs_entries = investigation.evidence.logs.logs or []
    logs_pack: Dict[str, Any] = {
        "status": investigation.evidence.logs.logs_status,
        "reason": investigation.evidence.logs.logs_reason,
        "backend": investigation.evidence.logs.logs_backend,
        "selector_used": investigation.evidence.logs.logs_query,
        "entries_count": len(logs_entries) if isinstance(logs_entries, list) else 0,
    }

    if include_logs:
        # Import redaction function for security
        try:
            from agent.authz.policy import redact_text
        except ImportError:
            # Fallback: no redaction if import fails
            def redact_text(s: str, **kwargs) -> str:
                return s

        trimmed = []
        for e in logs_entries[-10:] if isinstance(logs_entries, list) else []:
            if not isinstance(e, dict):
                continue
            raw_message = str(e.get("message", ""))
            # Redact secrets but preserve infrastructure names by default
            safe_message = redact_text(raw_message, redact_infrastructure=False)
            trimmed.append({"timestamp": str(e.get("timestamp")), "message": _truncate(safe_message, 240)})
        logs_pack["sample_tail"] = trimmed

    throttling_data = investigation.evidence.metrics.throttling_data or {}
    cpu_metrics = investigation.evidence.metrics.cpu_metrics or {}
    restart_data = investigation.evidence.metrics.restart_data or {}
    pod_phase_signal = investigation.evidence.metrics.pod_phase_signal or {}

    throttling_series = throttling_data.get("throttling_percentage") or []
    cpu_usage_series = cpu_metrics.get("cpu_usage") or []
    restart_series = restart_data.get("restart_increase_5m") or []
    pod_phase_series = pod_phase_signal.get("pod_phase_signal") or []

    # Warning events (tiny)
    warning_events = []
    for ev in (investigation.evidence.k8s.pod_events or [])[:20]:
        if not isinstance(ev, dict):
            continue
        if (ev.get("type") or "").lower() == "warning":
            warning_events.append(
                {
                    "reason": ev.get("reason"),
                    "message": _truncate(ev.get("message"), 200),
                    "last_timestamp": ev.get("last_timestamp") or ev.get("event_time") or ev.get("first_timestamp"),
                }
            )
        if len(warning_events) >= 3:
            break

    # Conditions with signal (non-True) plus PodScheduled.
    conds = []
    for c in investigation.evidence.k8s.pod_conditions or []:
        if not isinstance(c, dict):
            continue
        ctype = c.get("type")
        status = c.get("status")
        if ctype == "PodScheduled" or (status and status != "True"):
            conds.append(
                {
                    "type": ctype,
                    "status": status,
                    "reason": c.get("reason"),
                    "message": _truncate(c.get("message"), 200),
                }
            )
        if len(conds) >= 6:
            break

    # Include hypotheses from diagnostic pattern matching (if available)
    hypotheses_pack = []
    if investigation.analysis and investigation.analysis.hypotheses:
        for hyp in investigation.analysis.hypotheses[:3]:  # Top 3 hypotheses
            # Hypotheses are Pydantic models, use attribute access
            try:
                why_list = hyp.why[:3] if hyp.why else []
                hypotheses_pack.append(
                    {
                        "title": hyp.title,
                        "confidence": hyp.confidence_0_100,
                        "why": why_list,
                    }
                )
            except Exception:
                # Skip malformed hypotheses
                continue

    # Include parsed errors from logs (if available)
    parsed_errors = []
    if investigation.evidence.logs and investigation.evidence.logs.parsed_errors:
        for err in investigation.evidence.logs.parsed_errors[:5]:  # Top 5 errors
            try:
                parsed_errors.append(
                    {
                        "pattern": err.pattern_name if hasattr(err, "pattern_name") else None,
                        "sample": _truncate(err.sample_line if hasattr(err, "sample_line") else str(err), 200),
                    }
                )
            except Exception:
                continue

    pack: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "alert": {
            "alertname": labels.get("alertname"),
            "severity": labels.get("severity"),
            "summary": _truncate(annotations.get("summary"), 300),
            "description": _truncate(annotations.get("description"), 600),
            "runbook_url": annotations.get("runbook_url"),
        },
        "target": {
            "namespace": investigation.target.namespace,
            "pod": investigation.target.pod,
            "container": investigation.target.container,
            "playbook": investigation.target.playbook,
        },
        "kubernetes": {
            "phase": (
                (investigation.evidence.k8s.pod_info or {}).get("phase")
                if isinstance(investigation.evidence.k8s.pod_info, dict)
                else None
            ),
            "node": (
                (investigation.evidence.k8s.pod_info or {}).get("node_name")
                if isinstance(investigation.evidence.k8s.pod_info, dict)
                else None
            ),
            "conditions": conds,
            "warning_events": warning_events,
        },
        "metrics": {
            "cpu_throttling_pct_max": _series_max(throttling_series) if isinstance(throttling_series, list) else None,
            "cpu_usage_cores_max": _series_max(cpu_usage_series) if isinstance(cpu_usage_series, list) else None,
            "restart_increase_5m_max": _series_max(restart_series) if isinstance(restart_series, list) else None,
            "pod_unhealthy_phase_signal_max": (
                _series_max(pod_phase_series) if isinstance(pod_phase_series, list) else None
            ),
        },
        "logs": logs_pack,
        "diagnostics": {
            "hypotheses": hypotheses_pack,
            "parsed_errors": parsed_errors,
        },
    }
    return pack
