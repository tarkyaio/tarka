"""Investigation pipeline orchestrator (SSOT-first).

Investigation is the single source of truth. Playbooks populate evidence by mutating the investigation.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Dict

from agent.core.family import set_canonical_family
from agent.core.models import AlertInstance, Evidence, Investigation, TargetRef, TimeWindow
from agent.core.targets import extract_target_container, should_ignore_pod_label_for_jobs
from agent.core.time_window import parse_time_window
from agent.diagnostics.collect import collect_evidence_via_modules
from agent.diagnostics.engine import run_diagnostics
from agent.pipeline.capacity import analyze_capacity
from agent.pipeline.changes import analyze_changes
from agent.pipeline.enrich import build_family_enrichment
from agent.pipeline.families import derive_target_type, detect_family
from agent.pipeline.features import compute_features
from agent.pipeline.noise import analyze_noise, postprocess_noise
from agent.pipeline.scoring import score_investigation
from agent.pipeline.signals import enrich_investigation_with_signal_queries
from agent.pipeline.verdict import build_base_decision
from agent.playbooks import default_playbook, get_playbook_for_alert, nonpod_baseline_playbook
from agent.providers.alertmanager_provider import get_alert_context


def _env_bool(name: str, default: bool = False) -> bool:
    """Parse boolean from environment variable."""
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "y", "on")


def _normalize_alert_state(raw_state: Any) -> tuple[str, str]:
    """
    Normalize Alertmanager status.state into firing/resolved semantics.

    Alertmanager v2 uses status.state values like: active, suppressed, unprocessed.
    We treat active/suppressed/unprocessed as 'firing' and anything else as 'unknown' unless explicitly inactive.
    """
    s = str(raw_state or "").strip().lower()
    if s in ("active", "suppressed", "unprocessed"):
        return "firing", "expires_at"
    if s in ("inactive", "resolved"):
        return "resolved", "resolved_at"
    return "unknown", "unknown"


def _promote_workload_to_target(investigation: Investigation) -> None:
    if investigation.target.workload_kind and investigation.target.workload_name:
        return
    rs = investigation.evidence.k8s.rollout_status or {}
    if isinstance(rs, dict) and rs.get("kind") and rs.get("name"):
        investigation.target.workload_kind = rs.get("kind")
        investigation.target.workload_name = rs.get("name")
        return
    oc = investigation.evidence.k8s.owner_chain or {}
    if isinstance(oc, dict) and isinstance(oc.get("workload"), dict):
        wl = oc.get("workload") or {}
        investigation.target.workload_kind = wl.get("kind")
        investigation.target.workload_name = wl.get("name")
        return


def _promote_team_env_to_target(investigation: Investigation) -> None:
    """
    Best-effort org routing metadata extraction from K8s workload labels.
    Assumes playbooks may have populated `investigation.evidence.k8s.owner_chain`.
    """
    if investigation.target.team and investigation.target.environment:
        return

    # Fast-path: if alert labels already carry org routing metadata, promote it without K8s I/O.
    labels0 = investigation.alert.labels if isinstance(investigation.alert.labels, dict) else {}
    if not investigation.target.team:
        for k in ("team", "owner", "squad", "app.kubernetes.io/team"):
            v = labels0.get(k)
            if v is None:
                continue
            s = str(v).strip()
            if s:
                investigation.target.team = s
                break
    if not investigation.target.environment:
        for k in ("environment", "env", "tf_env", "app.kubernetes.io/environment"):
            v = labels0.get(k)
            if v is None:
                continue
            s = str(v).strip()
            if s:
                investigation.target.environment = s
                break
    if investigation.target.team and investigation.target.environment:
        return
    oc = investigation.evidence.k8s.owner_chain or {}
    if not isinstance(oc, dict):
        oc = {}

    # If owner_chain wasn't gathered (common for non-pod playbooks), try best-effort lookups using
    # scrape metadata (pod+namespace labels). This is useful for org routing labels like `team`
    # even when the incident target is a service.
    if not oc:
        pod = labels0.get("pod") or labels0.get("pod_name") or labels0.get("podName")
        ns = labels0.get("namespace") or labels0.get("Namespace") or investigation.target.namespace
        if pod and ns:
            try:
                from agent.providers.k8s_provider import get_pod_owner_chain

                oc = get_pod_owner_chain(str(pod), str(ns))
                investigation.evidence.k8s.owner_chain = oc
            except Exception:
                oc = {}

    wl = oc.get("workload") if isinstance(oc.get("workload"), dict) else None
    labels = wl.get("labels") if isinstance(wl, dict) and isinstance(wl.get("labels"), dict) else None

    if not investigation.target.team:
        t = labels.get("team") if isinstance(labels, dict) else None
        if t is None and isinstance(oc.get("pod_labels"), dict):
            t = (oc.get("pod_labels") or {}).get("team")
        investigation.target.team = str(t).strip() if t is not None and str(t).strip() else None
    if not investigation.target.environment:
        e = labels.get("environment") if isinstance(labels, dict) else None
        if e is None and isinstance(oc.get("pod_labels"), dict):
            e = (oc.get("pod_labels") or {}).get("environment")
        investigation.target.environment = str(e).strip() if e is not None and str(e).strip() else None


def run_investigation(
    *,
    alert: Dict[str, Any],
    time_window: str,
) -> Investigation:
    """
    Run an investigation for a single alert instance and return an Investigation.

    SSOT-first pipeline:
    - Build base Investigation from alert payload + time window
    - Run selected playbook to gather evidence
    - Run deterministic enrichment + scoring
    """
    alert_context = get_alert_context(alert)
    labels = alert_context.get("all_labels", {}) if isinstance(alert_context, dict) else {}
    alertname = labels.get("alertname") or "Unknown"

    # NEW: Use alert.starts_at as primary time anchor for historical investigations
    # This is critical for TTL-deleted pods where we need to look at the ACTUAL incident time
    # Handle both dict (raw alert payload) and AlertInstance (Investigation.alert)
    if isinstance(alert, dict):
        alert_starts_at = alert.get("starts_at")
    else:
        alert_starts_at = getattr(alert, "starts_at", None)
    if alert_starts_at:
        try:
            # Parse RFC3339 timestamp
            alert_start = datetime.fromisoformat(alert_starts_at.replace("Z", "+00:00"))

            # Calculate lookback duration using existing parse_time_window
            # We call it to get the duration, then adjust the anchor
            _, temp_end = parse_time_window(time_window)
            temp_start, _ = parse_time_window(time_window)
            duration = temp_end - temp_start

            # Use alert start time as anchor (NOT current time)
            start_time = alert_start - duration
            end_time = alert_start
        except Exception:
            # Fallback to default time window parsing if alert.starts_at is invalid
            start_time, end_time = parse_time_window(time_window)
    else:
        start_time, end_time = parse_time_window(time_window)

    tw = TimeWindow(window=time_window, start_time=start_time, end_time=end_time)

    # Determine canonical family early (before module selection / feature extraction).
    # We include a playbook hint (from the router) because alertnames don’t always contain
    # family substrings, but the playbook registry does.
    family_hint = "generic"
    playbook_hint = None
    if isinstance(labels, dict):
        try:
            pb = get_playbook_for_alert(str(alertname))
            pb_name = getattr(pb, "__name__", "") if pb is not None else ""
            n = str(pb_name).lower()
            if "cpu_throttling" in n:
                playbook_hint = "cpu_throttling"
            elif "pod_not_healthy" in n:
                playbook_hint = "pod_not_healthy"
            elif "oom_killer" in n or "oom" in n:
                playbook_hint = "oom_killer"
            elif "http_5xx" in n or "5xx" in n:
                playbook_hint = "http_5xx"
            elif "memory_pressure" in n:
                playbook_hint = "memory_pressure"
            elif "nonpod_baseline" in n:
                playbook_hint = "nonpod_baseline"
            elif "default" in n:
                playbook_hint = "default"
        except Exception:
            playbook_hint = None
        family_hint = detect_family(labels, playbook=playbook_hint)
    pod_name = labels.get("pod") or labels.get("pod_name") or labels.get("podName") or None
    namespace = labels.get("namespace") or labels.get("Namespace") or None

    # For Job alerts, ignore pod label (it's the scrape pod, not the job pod)
    # The job_failure collector will find the correct pod using job-name label selector
    if should_ignore_pod_label_for_jobs(labels):
        pod_name = None

    if family_hint in ("target_down", "k8s_rollout_health", "observability_pipeline", "meta"):
        pod_name = None

    alert_model = AlertInstance(
        fingerprint=str(alert.get("fingerprint") or ""),
        labels=dict(labels) if isinstance(labels, dict) else {},
        annotations=dict(alert.get("annotations") or {}) if isinstance(alert.get("annotations"), dict) else {},
        starts_at=alert.get("starts_at"),
        ends_at=alert.get("ends_at"),
        generator_url=alert.get("generator_url"),
        state=(alert.get("status") or {}).get("state") if isinstance(alert.get("status"), dict) else None,
    )
    norm_state, ends_kind = _normalize_alert_state(alert_model.state)
    alert_model.normalized_state = norm_state
    alert_model.ends_at_kind = ends_kind

    target = TargetRef(
        namespace=namespace,
        pod=pod_name,
        container=extract_target_container(labels) if isinstance(labels, dict) else None,
        service=(labels.get("service") if isinstance(labels, dict) else None),
        instance=(labels.get("instance") if isinstance(labels, dict) else None),
        job=(labels.get("job") if isinstance(labels, dict) else None),
        cluster=(labels.get("cluster") if isinstance(labels, dict) else None),
    )
    # Cluster fallback: if alerts don't carry a cluster label, use env CLUSTER_NAME.
    if not target.cluster:
        target.cluster = (os.getenv("CLUSTER_NAME") or "").strip() or None
    if isinstance(labels, dict):
        target.target_type = derive_target_type(labels, pod=target.pod, namespace=target.namespace)
        # For pod-scoped alerts, `service/job/instance` commonly refer to the *scrape target*
        # (e.g., kube-state-metrics), not the affected workload. Keep them in alert.labels but
        # don't treat them as incident target identity to avoid confusion and over-filtering.
        if target.target_type == "pod":
            target.service = None
            target.job = None
            target.instance = None

    investigation = Investigation(
        alert=alert_model,
        time_window=tw,
        target=target,
        evidence=Evidence(),
        errors=[],
        meta={"source": "pipeline"},
    )
    # Persist canonical family early for diagnostics and downstream debugging.
    set_canonical_family(investigation, family_hint, source="detect_family(alert_labels, playbook_hint)")
    if playbook_hint:
        investigation.meta["playbook_hint"] = str(playbook_hint)

    # Phase 0: universal diagnostic modules collect evidence (pivot away from alertname playbooks).
    did_collect = collect_evidence_via_modules(investigation)

    # Resilient fallback: if no diagnostic succeeded, run playbook-based collection.
    # This ensures evidence collection happens even if:
    # - No diagnostic module applies (new/unknown alert type)
    # - All diagnostic modules fail (bugs, missing methods, etc.)
    # Playbooks provide compatibility and graceful degradation.
    if not did_collect:
        playbook_func = get_playbook_for_alert(str(alertname))
        # Routing rule: if we’re using the generic default playbook, choose the default baseline
        # by target scope. This prevents non-pod alerts from accidentally running pod baseline.
        if playbook_func is None or playbook_func == default_playbook:
            playbook_func = default_playbook if target.target_type == "pod" else nonpod_baseline_playbook
        playbook_func(investigation)

    _promote_workload_to_target(investigation)
    _promote_team_env_to_target(investigation)

    # Optional AWS evidence (best-effort, never blocks pipeline)
    # Includes: EC2, EBS, ELB, RDS, ECR, networking health checks + CloudTrail infrastructure changes
    if _env_bool("AWS_EVIDENCE_ENABLED", False):
        try:
            from agent.collectors.aws_context import collect_aws_evidence

            aws_evidence = collect_aws_evidence(investigation)
            investigation.evidence.aws.ec2_instances = aws_evidence.get("ec2_instances", {})
            investigation.evidence.aws.ebs_volumes = aws_evidence.get("ebs_volumes", {})
            investigation.evidence.aws.elb_health = aws_evidence.get("elb_health", {})
            investigation.evidence.aws.rds_instances = aws_evidence.get("rds_instances", {})
            investigation.evidence.aws.ecr_images = aws_evidence.get("ecr_images", {})
            investigation.evidence.aws.networking = aws_evidence.get("networking", {})
            investigation.evidence.aws.metadata = aws_evidence.get("metadata")
        except Exception:
            pass  # Never block pipeline on AWS errors

        # CloudTrail is part of AWS evidence (not a separate flag)
        try:
            from agent.collectors.aws_context import collect_cloudtrail_events

            # Expand time window: investigation window + lookback for precursors
            lookback_minutes = int(os.getenv("AWS_CLOUDTRAIL_LOOKBACK_MINUTES", "30"))

            # Parse alert start time
            alert_start_str = investigation.alert.starts_at
            if alert_start_str:
                alert_start = datetime.fromisoformat(alert_start_str.replace("Z", "+00:00"))
            else:
                # Fallback: use current time - 1 hour
                alert_start = datetime.utcnow() - timedelta(hours=1)

            expanded_start = alert_start - timedelta(minutes=lookback_minutes)

            # Collect CloudTrail events
            max_events = int(os.getenv("AWS_CLOUDTRAIL_MAX_EVENTS", "50"))
            cloudtrail_evidence = collect_cloudtrail_events(investigation, expanded_start, max_events)

            if cloudtrail_evidence:
                investigation.evidence.aws.cloudtrail_events = cloudtrail_evidence.get("events")
                investigation.evidence.aws.cloudtrail_grouped = cloudtrail_evidence.get("grouped")
                investigation.evidence.aws.cloudtrail_metadata = cloudtrail_evidence.get("metadata")
        except Exception:
            pass  # Never block pipeline on CloudTrail errors

    # Optional GitHub evidence (best-effort)
    if _env_bool("GITHUB_EVIDENCE_ENABLED", False):
        try:
            from agent.collectors.github_context import collect_github_evidence

            gh_evidence = collect_github_evidence(investigation)
            investigation.evidence.github.repo = gh_evidence.get("repo")
            investigation.evidence.github.repo_discovery_method = gh_evidence.get("repo_discovery_method")
            investigation.evidence.github.is_third_party = gh_evidence.get("is_third_party", False)
            investigation.evidence.github.recent_commits = gh_evidence.get("recent_commits", [])
            investigation.evidence.github.workflow_runs = gh_evidence.get("workflow_runs", [])
            investigation.evidence.github.failed_workflow_logs = gh_evidence.get("failed_workflow_logs")
            investigation.evidence.github.readme = gh_evidence.get("readme")
            investigation.evidence.github.docs = gh_evidence.get("docs", [])
        except Exception:
            pass  # Never block pipeline on GitHub errors

    # Always compute noise (works without pod target).
    analyze_noise(investigation)

    # Signals: only run for non-pod investigations (e.g., http_5xx derived from labels).
    # Pod-scoped baseline evidence is collected by playbooks (shared pod baseline).
    if investigation.target.target_type != "pod":
        enrich_investigation_with_signal_queries(investigation)

    # Pod/workload analyses require a concrete pod target.
    has_pod_target = bool(
        investigation.target.pod
        and investigation.target.namespace
        and investigation.target.pod != "Unknown"
        and investigation.target.namespace != "Unknown"
    )
    if has_pod_target:
        analyze_changes(investigation)
        analyze_capacity(investigation)

    # Deterministic features -> scores -> verdict
    features = compute_features(investigation)
    investigation.analysis.features = features
    # Job-specific metrics (for Evidence display)
    from agent.pipeline.job_metrics import compute_job_metrics

    compute_job_metrics(investigation)
    # Noise postprocessing can use derived features (e.g., inferred container).
    postprocess_noise(investigation)
    # Base triage decision (scenario-driven); used by reports.
    investigation.analysis.decision = build_base_decision(investigation)
    # Family enrichment (additive; on-call-first)
    investigation.analysis.enrichment = build_family_enrichment(investigation)
    # Universal diagnostic modules (hypotheses + next tests), derived from SSOT.
    run_diagnostics(investigation, do_collect=False)
    scores, verdict = score_investigation(investigation, features)
    investigation.analysis.scores = scores
    investigation.analysis.verdict = verdict

    # Optional, additive LLM enrichment (does not affect deterministic verdict/scoring).
    # This populates `investigation.analysis.llm` when LLM_ENABLED=1, and never raises.
    try:
        from agent.llm.enrich_investigation import maybe_enrich_investigation

        maybe_enrich_investigation(investigation, enabled=False)
    except Exception:
        # If the module import fails for any reason, keep the investigation deterministic.
        pass
    return investigation
