"""Crashloop evidence collector.

Builds on the shared pod_baseline collector with crashloop-specific enrichment:
- Previous container logs (from K8s API, not log backend)
- Probe failure detection from pod events
- Crash timing analysis from container statuses
"""

from __future__ import annotations

from typing import List, Optional

from agent.collectors.log_parser import parse_log_entries
from agent.collectors.pod_baseline import (
    _container_from_investigation,
    _require_pod_target,
    collect_pod_baseline,
)
from agent.core.models import Investigation
from agent.providers.k8s_provider import get_k8s_provider


def collect_crashloop_evidence(investigation: Investigation) -> None:
    """Crashloop evidence collector.

    Steps:
    1. Set playbook identity
    2. Validate pod target
    3. Collect pod baseline (K8s context, metrics, logs) with increased events limit
    4. Fetch previous container logs via K8s API
    5. Parse previous logs for error patterns
    6. Detect probe failures from pod events
    7. Extract crash timing from container statuses

    Mutates investigation.evidence in-place. Never raises exceptions.
    """
    investigation.target.playbook = "crashloop"

    target = _require_pod_target(investigation, "crashloop")
    if target is None:
        return
    pod, namespace = target

    # Step 3: Collect full pod baseline (K8s context, metrics, logs)
    # Increased events_limit because crashloops generate many BackOff/Unhealthy events
    collect_pod_baseline(investigation, events_limit=50)

    container = _container_from_investigation(investigation)

    # Step 4: Fetch previous container logs via K8s API
    _fetch_previous_logs(investigation, pod, namespace, container)

    # Step 5: Parse previous logs for error patterns
    _parse_previous_logs(investigation)

    # Step 6: Detect probe failures from pod events
    _detect_probe_failures(investigation)

    # Step 7: Extract crash timing from container statuses
    _extract_crash_timing(investigation, container)


def _fetch_previous_logs(
    investigation: Investigation,
    pod: str,
    namespace: str,
    container: Optional[str],
) -> None:
    """Fetch logs from the previous terminated container instance."""
    try:
        k8s = get_k8s_provider()
        prev_logs = k8s.read_pod_log(
            pod_name=pod,
            namespace=namespace,
            container=container,
            previous=True,
            tail_lines=200,
        )
        if prev_logs:
            investigation.meta["previous_container_logs"] = prev_logs
    except Exception as e:
        investigation.errors.append(f"Failed to fetch previous container logs: {e}")


def _parse_previous_logs(investigation: Investigation) -> None:
    """Parse previous container logs for ERROR/FATAL patterns."""
    prev_logs_raw = investigation.meta.get("previous_container_logs")
    if not prev_logs_raw:
        return

    try:
        # Convert raw log text to list-of-dict format expected by parse_log_entries
        log_entries = [{"message": line} for line in prev_logs_raw.splitlines() if line.strip()]
        if not log_entries:
            return

        parse_result = parse_log_entries(log_entries, limit=50)
        parsed_errors = parse_result.get("parsed_errors", [])
        if parsed_errors:
            investigation.meta["previous_logs_parsed_errors"] = parsed_errors
    except Exception as e:
        investigation.errors.append(f"Previous log parsing failed: {e}")


def _detect_probe_failures(investigation: Investigation) -> None:
    """Detect liveness/readiness probe failures from pod events."""
    events = investigation.evidence.k8s.pod_events
    if not events:
        investigation.meta["probe_failure_type"] = None
        return

    probe_type = None
    for ev in events:
        if not isinstance(ev, dict):
            continue
        reason = (ev.get("reason") or "").strip()
        message = (ev.get("message") or "").strip().lower()

        if reason == "Unhealthy":
            if "liveness" in message:
                probe_type = "liveness"
                break  # Liveness is highest priority (causes restarts)
            elif "readiness" in message:
                if probe_type is None:
                    probe_type = "readiness"

    investigation.meta["probe_failure_type"] = probe_type


def _extract_crash_timing(investigation: Investigation, container: Optional[str]) -> None:
    """Extract crash duration from container statuses.

    Calculates time between container start and termination to distinguish:
    - Instant crashes (<10s): likely config/dependency issues
    - Slow crashes (>60s): likely memory leak/timeout issues
    """
    pod_info = investigation.evidence.k8s.pod_info
    if not isinstance(pod_info, dict):
        return

    container_statuses: List[dict] = pod_info.get("container_statuses", [])
    if not container_statuses:
        return

    # Find the target container (or first one)
    target_cs = None
    for cs in container_statuses:
        if not isinstance(cs, dict):
            continue
        if container and cs.get("name") == container:
            target_cs = cs
            break
    if target_cs is None and container_statuses:
        target_cs = container_statuses[0] if isinstance(container_statuses[0], dict) else None

    if target_cs is None:
        return

    # Extract crash duration from last_state.terminated
    last_state = target_cs.get("last_state")
    if not isinstance(last_state, dict):
        return

    terminated = last_state.get("terminated")
    if not isinstance(terminated, dict):
        return

    started_at = terminated.get("startedAt") or terminated.get("started_at")
    finished_at = terminated.get("finishedAt") or terminated.get("finished_at")

    if started_at and finished_at:
        try:
            from datetime import datetime

            # Parse ISO timestamps
            start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
            finish = datetime.fromisoformat(str(finished_at).replace("Z", "+00:00"))
            duration_seconds = max(0, int((finish - start).total_seconds()))
            investigation.meta["crash_duration_seconds"] = duration_seconds
        except Exception:
            pass


__all__ = ["collect_crashloop_evidence"]
