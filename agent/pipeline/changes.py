"""Change timeline + correlation (K8s-first, read-only).

This is intentionally lightweight and evidence-first:
- pull rollout/condition timestamps from the owning workload (Deployment/StatefulSet/DaemonSet)
- correlate last known rollout change with the incident time window
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from agent.core.models import ChangeCorrelation, ChangeEvent, ChangeTimeline, Investigation


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        # datetime.fromisoformat doesn't handle Z; normalize
        s = str(ts).replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _ensure_aware_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _max_dt(dts: List[Optional[datetime]]) -> Optional[datetime]:
    xs = [x for x in dts if isinstance(x, datetime)]
    return max(xs) if xs else None


def build_k8s_change_timeline_from_investigation(investigation: Investigation) -> ChangeTimeline:
    """
    Investigation-native timeline builder.
    """
    namespace = investigation.target.namespace or "Unknown"
    owner_chain = investigation.evidence.k8s.owner_chain
    rollout_status = investigation.evidence.k8s.rollout_status

    workload = None
    events: List[ChangeEvent] = []

    if isinstance(owner_chain, dict):
        wl = owner_chain.get("workload")
        if isinstance(wl, dict) and wl.get("kind") and wl.get("name"):
            workload = {"kind": wl.get("kind"), "name": wl.get("name"), "namespace": namespace}

        for o in owner_chain.get("owners") or []:
            if not isinstance(o, dict):
                continue
            if o.get("kind") == "ReplicaSet" and o.get("name"):
                events.append(
                    ChangeEvent(
                        timestamp=None,
                        kind="ReplicaSet",
                        name=str(o.get("name")),
                        namespace=namespace,
                        reason="owner_chain",
                        message="Pod owned by ReplicaSet (often created during Deployment rollout).",
                    )
                )

    last_dt_candidates: List[Optional[datetime]] = []
    if isinstance(rollout_status, dict) and rollout_status.get("kind") and rollout_status.get("name"):
        wk = str(rollout_status.get("kind"))
        wn = str(rollout_status.get("name"))
        workload = {"kind": wk, "name": wn, "namespace": namespace}

        created = rollout_status.get("creation_timestamp")
        if created:
            events.append(
                ChangeEvent(
                    timestamp=str(created),
                    kind=wk,
                    name=wn,
                    namespace=namespace,
                    reason="created",
                    message="Workload creation timestamp.",
                )
            )
            last_dt_candidates.append(_parse_iso(str(created)))

        rev = rollout_status.get("revision")
        if rev:
            events.append(
                ChangeEvent(
                    timestamp=None,
                    kind=wk,
                    name=wn,
                    namespace=namespace,
                    reason="revision",
                    message=f"Current rollout revision: {rev}",
                )
            )

        for c in rollout_status.get("conditions") or []:
            if not isinstance(c, dict):
                continue
            ctype = c.get("type")
            status = c.get("status")
            reason = c.get("reason")
            msg = c.get("message")
            ts = c.get("last_update_time") or c.get("last_transition_time")
            if ts:
                last_dt_candidates.append(_parse_iso(str(ts)))
            if ctype and status:
                events.append(
                    ChangeEvent(
                        timestamp=str(ts) if ts else None,
                        kind=wk,
                        name=wn,
                        namespace=namespace,
                        reason=f"condition:{ctype}:{status}:{reason}",
                        message=str(msg)[:240] if msg else None,
                    )
                )

        imgs = rollout_status.get("images") or []
        if isinstance(imgs, list) and imgs:
            for img in imgs[:10]:
                if not isinstance(img, dict):
                    continue
                cname = img.get("name")
                cimg = img.get("image")
                if cname and cimg:
                    events.append(
                        ChangeEvent(
                            timestamp=None,
                            kind=wk,
                            name=wn,
                            namespace=namespace,
                            reason="image",
                            message=f"{cname} -> {cimg}",
                        )
                    )

    last_dt = _max_dt(last_dt_candidates)
    return ChangeTimeline(
        source="kubernetes",
        workload=workload,
        events=events,
        last_change_time=last_dt.isoformat() if last_dt else None,
    )


def correlate_changes_for_investigation(investigation: Investigation, timeline: ChangeTimeline) -> ChangeCorrelation:
    start_time = _ensure_aware_utc(investigation.time_window.start_time)
    end_time = _ensure_aware_utc(investigation.time_window.end_time)
    if start_time is None or end_time is None:
        return ChangeCorrelation(
            has_recent_change=False,
            score=0.0,
            summary="Incident time window is missing; cannot correlate changes.",
            last_change_time=None,
            timeline=timeline,
        )

    last_dt = _parse_iso(timeline.last_change_time) if timeline.last_change_time else None
    last_dt = _ensure_aware_utc(last_dt)
    window_seconds = max(1.0, (end_time - start_time).total_seconds())

    if not last_dt:
        return ChangeCorrelation(
            has_recent_change=False,
            score=0.0,
            summary="No workload change timestamp found to correlate with this incident window.",
            last_change_time=None,
            timeline=timeline,
        )

    delta_seconds = abs((end_time - last_dt).total_seconds())
    within_window = start_time <= last_dt <= end_time

    if within_window:
        score = 0.9
        summary = "A workload change occurred within the incident time window (high correlation likelihood)."
    elif delta_seconds <= 2.0 * window_seconds:
        score = 0.5
        summary = "A workload change occurred near the incident window (moderate correlation likelihood)."
    elif delta_seconds <= 6.0 * window_seconds:
        score = 0.2
        summary = "A workload change occurred, but not near the incident window (low correlation likelihood)."
    else:
        score = 0.1
        summary = "Workload change appears far from the incident window (very low correlation likelihood)."

    return ChangeCorrelation(
        has_recent_change=bool(within_window),
        score=score,
        summary=summary,
        last_change_time=last_dt.isoformat(),
        timeline=timeline,
    )


def analyze_changes(investigation: Investigation) -> None:
    """Populate investigation.analysis.change (never raises)."""
    try:
        timeline = build_k8s_change_timeline_from_investigation(investigation)
        investigation.analysis.change = correlate_changes_for_investigation(investigation, timeline)
    except Exception as e:
        investigation.errors.append(f"Changes: {e}")
        return
