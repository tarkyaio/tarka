"""Curated signal queries (Prometheus + logs) to enrich investigations.

Goal: ensure every incident has a baseline evidence slice even when a playbook is minimal.
This is deterministic and read-only (no LLM required).
"""

from __future__ import annotations

from typing import Optional

from agent.core.models import Investigation
from agent.core.targets import extract_target_container


def _extract_container_from_investigation(investigation: Investigation) -> Optional[str]:
    # Prefer explicit target container; else fall back to alert labels.
    if investigation.target and investigation.target.container:
        return investigation.target.container
    labels = investigation.alert.labels or {}
    if isinstance(labels, dict):
        return extract_target_container(labels)
    return None


def _should_try_http_5xx(investigation: Investigation) -> bool:
    alertname = (
        (investigation.alert.labels or {}).get("alertname") if isinstance(investigation.alert.labels, dict) else ""
    )
    playbook = investigation.target.playbook or ""
    txt = f"{alertname} {playbook}".lower()
    return "5xx" in txt or "http" in txt


def enrich_investigation_with_signal_queries(investigation: Investigation) -> None:
    """
    Investigation-native signal enrichment.

    Populates `investigation.evidence.metrics` and `investigation.evidence.logs` if missing.
    Never raises.
    """
    try:
        # HTTP 5xx can be derived from alert labels even when there is no pod target.
        if investigation.evidence.metrics.http_5xx is None and _should_try_http_5xx(investigation):
            try:
                from agent.providers.prom_provider import query_http_5xx_generic

                labels = investigation.alert.labels or {}
                if isinstance(labels, dict):
                    investigation.evidence.metrics.http_5xx = query_http_5xx_generic(
                        labels=labels,
                        start_time=investigation.time_window.start_time,
                        end_time=investigation.time_window.end_time,
                    )
            except Exception as e:
                investigation.errors.append(f"Signals: http_5xx: {e}")

        # Pod-scoped baseline signals are collected by playbooks (shared pod baseline in agent/playbooks.py).
        # Keep signals minimal to avoid duplicated queries.
        return
    except Exception:
        return
