from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, Optional

from jinja2 import Environment, StrictUndefined, UndefinedError

from agent.sources.config import SourceConfig

logger = logging.getLogger(__name__)

# Shared Jinja2 environment — StrictUndefined so template errors surface clearly
# in logs, but we catch UndefinedError per-field and treat it as empty string.
_jinja_env = Environment(undefined=StrictUndefined)

# Register convenience tests usable in select/reject filters:
#   {{ tags | select('startswith', 'team-') | first | default('unknown') }}
#   {{ tags | select('endswith', '-prod') | list }}
_jinja_env.tests["startswith"] = lambda value, prefix: str(value).startswith(str(prefix))
_jinja_env.tests["endswith"] = lambda value, suffix: str(value).endswith(str(suffix))


def _render(template_str: str, payload: Any) -> str:
    """
    Render a single Jinja2 template with ``payload`` as the sole context variable.

    Returns an empty string if the template references an undefined path so that
    optional fields can be left unset without crashing.
    """
    try:
        tmpl = _jinja_env.from_string(template_str)
        result = tmpl.render(payload=payload)
        return result.strip()
    except UndefinedError as e:
        logger.debug("Jinja2 undefined in template %r: %s", template_str, e)
        return ""
    except Exception as e:
        logger.warning("Jinja2 render error in template %r: %s", template_str, e)
        return ""


def _fallback_fingerprint(source_id: str, alertname: str) -> str:
    raw = f"{source_id}:{alertname}"
    return hashlib.sha256(raw.encode()).hexdigest()


def normalize_http_event(payload: Any, config: SourceConfig) -> Dict[str, Any]:
    """
    Apply the source's Jinja2 field_map to an incoming payload and return a
    normalised alert dict in the same shape as ``_normalize_webhook_alert()``
    in webhook.py:

        {
          "fingerprint": str,
          "labels":      {str: str},
          "annotations": {str: str},
          "starts_at":   str | None,
          "ends_at":     str | None,
          "generator_url": str,
          "status":      {"state": "firing" | "resolved" | "unknown"},
        }

    Raises ValueError for missing required fields so the caller can return HTTP 400.
    """
    fm = config.field_map

    # --- Required fields ---
    fingerprint = _render(fm.fingerprint, payload)
    alertname = _render(fm.alertname, payload)
    raw_status = _render(fm.status, payload)

    if not fingerprint:
        raise ValueError("field_map.fingerprint rendered to an empty string — check your template")
    if not alertname:
        raise ValueError("field_map.alertname rendered to an empty string — check your template")
    if not raw_status:
        raise ValueError("field_map.status rendered to an empty string — check your template")

    # Normalise status to firing / resolved / unknown
    status_lower = raw_status.lower()
    if status_lower == "firing":
        state: str = "firing"
    elif status_lower == "resolved":
        state = "resolved"
    else:
        logger.warning(
            "source=%s fingerprint=%s: status %r is not firing/resolved — treating as unknown",
            config.id,
            fingerprint,
            raw_status,
        )
        state = "unknown"

    # --- Optional fields ---
    severity: Optional[str] = _render(fm.severity, payload) if fm.severity else None
    summary: Optional[str] = _render(fm.summary, payload) if fm.summary else None
    description: Optional[str] = _render(fm.description, payload) if fm.description else None
    source_url: Optional[str] = _render(fm.source_url, payload) if fm.source_url else None
    starts_at: Optional[str] = _render(fm.starts_at, payload) if fm.starts_at else None
    ends_at: Optional[str] = _render(fm.ends_at, payload) if fm.ends_at else None

    # --- Labels ---
    labels: Dict[str, str] = {}
    labels["alertname"] = alertname
    labels["source_id"] = config.id
    if severity:
        labels["severity"] = severity

    # Render extra_labels (values are also Jinja2 templates)
    for k, v_template in config.extra_labels.items():
        rendered = _render(v_template, payload)
        if rendered:
            labels[k] = rendered

    # --- Annotations ---
    annotations: Dict[str, str] = {}
    if summary:
        annotations["summary"] = summary
    if description:
        annotations["description"] = description
    if source_url:
        annotations["source_url"] = source_url

    return {
        "fingerprint": fingerprint,
        "labels": labels,
        "annotations": annotations,
        "starts_at": starts_at or None,
        "ends_at": ends_at or None,
        "generator_url": source_url or "",
        "status": {"state": state},
        "source": f"http:{config.id}",
    }
