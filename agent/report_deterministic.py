"""Deterministic report renderer (concise + appendix).

The concise section must depend only on computed:
- investigation.analysis.features
- investigation.analysis.scores
- investigation.analysis.verdict

Appendix includes raw evidence for debugging (still deterministic formatting).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from agent.authz.policy import load_action_policy
from agent.core.models import Investigation
from agent.logs_select import select_snippet_latest_error_with_context
from agent.memory.config import load_memory_config
from agent.pipeline.enrich import build_family_enrichment
from agent.pipeline.features import compute_features
from agent.pipeline.scoring import score_investigation
from agent.pipeline.verdict import build_base_decision


def _is_command_line(s: str) -> bool:
    """Check if a string looks like a shell command or query that should be rendered as code."""
    s_stripped = s.strip()
    # Empty lines and markdown code fences are not commands
    if not s_stripped or s_stripped.startswith("```"):
        return False
    # Check for common command prefixes
    command_prefixes = ("kubectl", "aws", "gcloud", "curl", "docker", "helm", "git", "python", "pip", "npm", "yarn")
    if any(s_stripped.startswith(cmd) for cmd in command_prefixes):
        return True
    # Check for PromQL queries (contain metric selectors with braces and common functions)
    if any(pattern in s_stripped for pattern in ("ALERTS{", "kube_", "rate(", "sum(", "increase(", "count(")):
        if "{" in s_stripped and ("=" in s_stripped or "}" in s_stripped):
            return True
    return False


def _render_next_steps(steps: List[str], lines: List[str]) -> None:
    """Render next steps with smart code block detection.

    Rules:
    - Text descriptions: rendered as bullet points
    - Command lines (kubectl, aws, etc.): rendered as code blocks
    - Multi-line code blocks (```...```): rendered as-is (no bullet prefix)
    - Empty lines: preserved for spacing
    """
    i = 0
    while i < len(steps):
        step = steps[i]

        # Handle multi-line code blocks (```json...```)
        if step.strip().startswith("```"):
            # Collect all lines until closing ```
            code_block = [step]
            i += 1
            while i < len(steps) and not steps[i].strip().startswith("```"):
                code_block.append(steps[i])
                i += 1
            # Add closing ``` if found
            if i < len(steps):
                code_block.append(steps[i])
                i += 1
            # Render code block without bullet points
            lines.extend(code_block)
            continue

        # Handle empty lines (spacing)
        if not step.strip():
            lines.append("")
            i += 1
            continue

        # Handle command lines (kubectl, aws, etc.)
        if _is_command_line(step):
            lines.append(f"```bash\n{step}\n```")
            i += 1
            continue

        # Default: render as bullet point (descriptive text)
        lines.append(f"- {step}")
        i += 1


def render_deterministic_report(investigation: Investigation, *, generated_at: Optional[datetime] = None) -> str:
    ts = generated_at or datetime.now(timezone.utc)
    # Treat naive timestamps as UTC to avoid ambiguity in reports/tests.
    if isinstance(ts, datetime) and ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    alertname = (
        (investigation.alert.labels or {}).get("alertname") if isinstance(investigation.alert.labels, dict) else None
    )
    severity = (
        (investigation.alert.labels or {}).get("severity") if isinstance(investigation.alert.labels, dict) else None
    )
    severity_txt = str(severity) if severity is not None else "unknown"

    lines: List[str] = []
    lines.append(f"# Incident Report: {alertname or 'Unknown'}")
    lines.append("")
    lines.append(f"**Alert:** `{alertname or 'Unknown'}`")
    lines.append(f"**Severity:** `{severity_txt}`")
    lines.append(f"**Target type:** `{investigation.target.target_type}`")
    if getattr(investigation.target, "environment", None):
        lines.append(f"**Environment:** `{investigation.target.environment}`")
    if investigation.target.target_type == "pod":
        lines.append(f"**Namespace:** `{investigation.target.namespace or 'Unknown'}`")
        lines.append(f"**Pod:** `{investigation.target.pod or 'Unknown'}`")
        if investigation.target.container:
            lines.append(f"**Container:** `{investigation.target.container}`")
        # Clarify scrape/metric source vs affected target (common for kube-state-metrics-driven alerts).
        labels = investigation.alert.labels if isinstance(investigation.alert.labels, dict) else {}
        job = labels.get("job")
        svc = labels.get("service")
        inst = labels.get("instance")
        scrape_container = labels.get("container")
        parts = []
        if job:
            parts.append(f"job={job}")
        if svc:
            parts.append(f"service={svc}")
        if inst:
            parts.append(f"instance={inst}")
        if scrape_container and (
            not investigation.target.container or scrape_container != investigation.target.container
        ):
            parts.append(f"scrape_container={scrape_container}")
        if parts:
            lines.append(f"**Metric source (scrape metadata):** `{', '.join(parts)}`")
    elif investigation.target.target_type == "service":
        lines.append(f"**Namespace:** `{investigation.target.namespace or 'Unknown'}`")
        lines.append(f"**Service:** `{investigation.target.service or 'Unknown'}`")
    elif investigation.target.target_type == "node":
        lines.append(f"**Instance:** `{investigation.target.instance or 'Unknown'}`")
    elif investigation.target.target_type == "cluster":
        lines.append(f"**Cluster:** `{investigation.target.cluster or 'Unknown'}`")
    else:
        # Best-effort fallback
        lines.append(f"**Namespace:** `{investigation.target.namespace or 'Unknown'}`")
        lines.append(f"**Pod:** `{investigation.target.pod or 'Unknown'}`")
    lines.append(f"**Time Window:** `{investigation.time_window.window}`")
    if investigation.alert.normalized_state:
        lines.append(f"**Alert state:** `{investigation.alert.normalized_state}`")
    if investigation.alert.starts_at:
        lines.append(f"**Alert starts_at:** `{investigation.alert.starts_at}`")
    lines.append(f"**Generated:** {ts.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    verdict = investigation.analysis.verdict
    scores = investigation.analysis.scores
    features = investigation.analysis.features
    decision = investigation.analysis.decision
    enrichment = investigation.analysis.enrichment

    # Ensure deterministic outputs exist even when report is invoked without prior scoring.
    if features is None:
        features = compute_features(investigation)
        investigation.analysis.features = features
    if decision is None:
        decision = build_base_decision(investigation)
        investigation.analysis.decision = decision
    if enrichment is None:
        enrichment = build_family_enrichment(investigation)
        investigation.analysis.enrichment = enrichment
    if scores is None or verdict is None:
        scores, verdict = score_investigation(investigation, features)
        investigation.analysis.scores = scores
        investigation.analysis.verdict = verdict

    # Base triage section (on-call first)
    if decision is not None:
        lines.append("## Triage")
        lines.append("")
        lines.append(f"**Summary:** {decision.label or 'n/a'}")
        if decision.why:
            lines.append("")
            lines.append("### Why")
            lines.append("")
            for w in decision.why[:10]:
                lines.append(f"- {w}")
        if decision.next:
            lines.append("")
            lines.append("### To unblock")
            lines.append("")
            # Use smart formatting for code blocks
            _render_next_steps(decision.next[:7], lines)
        lines.append("")

    # Family enrichment section (additive, on-call-first)
    if enrichment is not None:
        lines.append("## Enrichment")
        lines.append("")
        lines.append(f"**Summary:** {enrichment.label or 'n/a'}")
        if enrichment.why:
            lines.append("")
            lines.append("### Why")
            lines.append("")
            for w in enrichment.why[:10]:
                lines.append(f"- {w}")
        if enrichment.next:

            def _fmt_next_e(s: str) -> str:
                txt = (s or "").strip()
                if not txt:
                    return ""
                if txt.startswith(("If ", "Note", "Interpretation", "Check ", "Follow ", "Otherwise")):
                    return txt
                if txt.startswith("kubectl "):
                    return f"`{txt}`"
                if (
                    any(k in txt for k in ("ALERTS{", "kube_", "up{", "count(", "topk(", "increase("))
                    or ("{" in txt and "}" in txt)
                    or any(
                        fn in txt
                        for fn in (
                            "rate(",
                            "sum(",
                            "avg(",
                            "max(",
                            "min(",
                            "histogram_quantile(",
                            "quantile_over_time(",
                        )
                    )
                ):
                    return f"`{txt}`"
                return txt

            lines.append("")
            lines.append("### On-call next")
            lines.append("")
            for s in enrichment.next[:7]:
                item = _fmt_next_e(s)
                if item:
                    lines.append(f"- {item}")
        lines.append("")

    # Diagnostic hypotheses (universal modules; deterministic)
    hyps = list(investigation.analysis.hypotheses or [])
    if hyps:
        ap = load_action_policy()
        lines.append("## Likely causes (ranked)")
        lines.append("")
        for h in hyps[:3]:
            lines.append(f"### {h.title} ({int(h.confidence_0_100)}/100)")
            if h.why:
                lines.append("")
                for w in h.why[:6]:
                    lines.append(f"- {w}")
            if h.next_tests:
                lines.append("")
                lines.append("**Next tests:**")
                lines.append("")
                # Use smart formatting for code blocks (no limit - remediation can be lengthy)
                _render_next_steps(h.next_tests, lines)

            # Policy-gated action suggestions (not executed by agent).
            if ap.enabled and getattr(h, "proposed_actions", None):
                acts = list(h.proposed_actions or [])
                if acts:
                    lines.append("")
                    lines.append("**Suggested actions (approval required):**")
                    lines.append("")
                    for a in acts[:3]:
                        risk = f" (risk: {a.risk})" if getattr(a, "risk", None) else ""
                        lines.append(f"- {a.title}{risk}")
                        pres = list(getattr(a, "preconditions", None) or [])
                        for ptxt in pres[:2]:
                            if (ptxt or "").strip():
                                lines.append(f"  - {ptxt}")
            lines.append("")

    # Optional memory section (non-deterministic; guarded by MEMORY_ENABLED=1)
    try:
        mem_cfg = load_memory_config()
    except Exception:
        mem_cfg = None
    if mem_cfg is not None and mem_cfg.memory_enabled:
        lines.append("## Memory")
        lines.append("")
        # Similar cases (best-effort)
        try:
            from agent.memory.case_retrieval import find_similar_runs

            ok, msg, sims = find_similar_runs(investigation, limit=5)
            if ok and sims:
                lines.append("### Similar cases")
                lines.append("")
                for s in sims:
                    key = s.s3_report_key or ""
                    suffix = f" (report_key={key})" if key else ""
                    one = s.one_liner.strip() if s.one_liner else ""
                    one_txt = one if one else "n/a"
                    res_bits = []
                    if getattr(s, "resolution_category", None):
                        res_bits.append(f"resolved={getattr(s, 'resolution_category')}")
                    if getattr(s, "postmortem_link", None):
                        res_bits.append("link=yes")
                    res_suffix = f" ({', '.join(res_bits)})" if res_bits else ""
                    lines.append(f"- case_id={s.case_id} run_id={s.run_id}: {one_txt}{suffix}{res_suffix}")
                lines.append("")
            elif ok:
                lines.append("- Similar cases: none found")
                lines.append("")
            else:
                lines.append(f"- Similar cases: unavailable ({msg})")
                lines.append("")
        except Exception:
            lines.append("- Similar cases: unavailable")
            lines.append("")

        # Matched skills (best-effort)
        try:
            from agent.memory.skills import match_skills

            ok, msg, matches = match_skills(investigation, max_matches=5)
            if ok and matches:
                lines.append("### Matched skills (suggest-only)")
                lines.append("")
                for m in matches:
                    lines.append(f"- **{m.skill.name}** (v{m.skill.version})")
                    if m.rendered.strip():
                        for ln in m.rendered.strip().splitlines():
                            lines.append(f"  {ln}")
                lines.append("")
            elif ok:
                lines.append("- Matched skills: none")
                lines.append("")
            else:
                lines.append(f"- Matched skills: unavailable ({msg})")
                lines.append("")
        except Exception:
            lines.append("- Matched skills: unavailable")
            lines.append("")

    # Concise section
    lines.append("## Verdict")
    lines.append("")
    lines.append(f"**Classification:** `{verdict.classification}`")
    lines.append(f"**Primary driver:** `{verdict.primary_driver}`")
    lines.append("")
    lines.append(verdict.one_liner)
    # Alert age / long-running hint (in concise section)
    if features.quality.alert_age_hours is not None:
        age_h = float(features.quality.alert_age_hours)
        if age_h >= 24:
            age_txt = f"~{age_h/24:.1f}d"
        else:
            age_txt = f"~{age_h:.1f}h"
        if features.quality.is_long_running:
            lines.append(f"- **Alert age:** {age_txt} (**long-running**)")
        else:
            lines.append(f"- **Alert age:** {age_txt}")
    lines.append("")

    lines.append("## Scores")
    lines.append("")
    lines.append(f"- **Impact:** {scores.impact_score}/100")
    lines.append(f"- **Confidence:** {scores.confidence_score}/100")
    lines.append(f"- **Noise:** {scores.noise_score}/100")
    lines.append("")

    if scores.reason_codes:
        lines.append("## Reason codes")
        lines.append("")
        for c in scores.reason_codes[:12]:
            lines.append(f"- `{c}`")
        lines.append("")

    # Noise insights (concise)
    ni = investigation.analysis.noise
    if ni is not None:
        flap_score = ni.flap.flap_score_0_100 if ni.flap is not None else 0
        missing = ni.missing_labels.missing if ni.missing_labels is not None else []
        eph = ni.cardinality.ephemeral_labels_present if ni.cardinality is not None else []

        show_noise = bool(missing) or bool(eph) or (flap_score >= 40)
        if show_noise:
            lines.append("## Noise insights")
            lines.append("")
            if flap_score:
                lines.append(
                    f"- **Flap score (0–100):** {flap_score} (lookback={ni.flap.lookback if ni.flap else 'n/a'})"
                )
            if eph:
                lines.append(f"- **High-cardinality labels present:** {', '.join(eph)}")
                if ni.cardinality and ni.cardinality.recommended_group_by:
                    lines.append(
                        f"- **Suggested Alertmanager group_by:** {', '.join(ni.cardinality.recommended_group_by)}"
                    )
            if missing:
                lines.append(f"- **Missing critical labels:** {', '.join(missing)}")
                recs = ni.missing_labels.recommendation if ni.missing_labels is not None else []
                if recs:
                    lines.append(f"- **Recommendation:** {recs[0]}")
                    if len(recs) > 1:
                        lines.append(f"- **Also:** {recs[1]}")
                else:
                    lines.append(
                        "- **Recommendation:** add missing labels in alert rules/relabeling so investigations can correlate evidence."
                    )
            lines.append("")

    if verdict.next_steps:
        lines.append("## On-call next steps")
        lines.append("")
        # Show all steps (no limit) - remediation steps can be lengthy with policies/configs
        # Smart formatting: detect code blocks and command snippets
        _render_next_steps(verdict.next_steps, lines)
        lines.append("")

    # Optional RCA section (provider-agnostic; intended to be evidence-grounded)
    rca = getattr(investigation.analysis, "rca", None)
    if rca is not None:
        lines.append("## Root cause analysis (RCA)")
        lines.append("")
        try:
            status = getattr(rca, "status", None) or "unknown"
            lines.append(f"- **Status:** `{status}`")
            if getattr(rca, "confidence_0_1", None) is not None:
                lines.append(f"- **Confidence:** {float(rca.confidence_0_1):.2f}")
            if getattr(rca, "summary", None):
                lines.append(f"- **Summary:** {rca.summary}")
            if getattr(rca, "root_cause", None):
                lines.append(f"- **Root cause:** {rca.root_cause}")
            evs = list(getattr(rca, "evidence", None) or [])
            if evs:
                lines.append("")
                lines.append("### Evidence cited")
                lines.append("")
                for e in evs[:6]:
                    if str(e or "").strip():
                        lines.append(f"- {e}")
            rem = list(getattr(rca, "remediation", None) or [])
            if rem:
                lines.append("")
                lines.append("### Remediation")
                lines.append("")
                for r in rem[:8]:
                    if str(r or "").strip():
                        lines.append(f"- {r}")
            unk = list(getattr(rca, "unknowns", None) or [])
            if unk:
                lines.append("")
                lines.append("### Unknowns / open questions")
                lines.append("")
                for u in unk[:6]:
                    if str(u or "").strip():
                        lines.append(f"- {u}")
            lines.append("")
        except Exception:
            # Never fail report rendering due to RCA formatting.
            pass

    # Optional LLM section (additive; shown whether or not scoring exists)
    if investigation.analysis.llm is not None:
        llm = investigation.analysis.llm
        lines.append("## LLM Insights")
        lines.append("")
        # Support both shapes:
        # - current: LLMInsights(provider, status, model, error, output)
        # - potential future: nested status object
        provider = (
            getattr(llm, "provider", None) or getattr(getattr(llm, "status", None), "provider", None) or "unknown"
        )
        status = getattr(llm, "status", None)
        status_txt = status if isinstance(status, str) else getattr(status, "status", None) or "unknown"
        model = getattr(llm, "model", None) or getattr(getattr(llm, "status", None), "model", None)
        error = getattr(llm, "error", None) or getattr(getattr(llm, "status", None), "error", None)
        output = getattr(llm, "output", None)

        lines.append(f"- **Provider:** `{provider}`")
        lines.append(f"- **Status:** `{status_txt}`")
        if model:
            lines.append(f"- **Model:** `{model}`")
        if error:
            lines.append(f"- **Error:** `{error}`")
        if isinstance(output, dict) and output:
            if output.get("summary"):
                lines.append(f"- **Summary:** {output.get('summary')}")
            if output.get("likely_root_cause"):
                lines.append(f"- **Likely root cause:** {output.get('likely_root_cause')}")
        lines.append("")

    # Appendix (raw evidence)
    lines.append("## Appendix: Evidence")
    lines.append("")

    lines.append("### Derived features")
    lines.append("")
    lines.append(f"- **Family:** `{features.family}`")
    lines.append(f"- **Evidence quality:** `{features.quality.evidence_quality or 'unknown'}`")
    if features.quality.alert_age_hours is not None:
        lines.append(f"- **Alert age (hours):** {features.quality.alert_age_hours:.1f}")
        if features.quality.is_long_running is not None:
            lines.append(f"- **is_long_running:** {features.quality.is_long_running}")
        if features.quality.is_recently_started is not None:
            lines.append(f"- **is_recently_started:** {features.quality.is_recently_started}")
    if features.quality.impact_signals_available is not None:
        lines.append(f"- **impact_signals_available:** {features.quality.impact_signals_available}")
        if features.quality.missing_impact_signals:
            lines.append(f"- **missing_impact_signals:** {', '.join(features.quality.missing_impact_signals)}")
    if features.quality.missing_inputs:
        lines.append(f"- **Missing inputs:** {', '.join(features.quality.missing_inputs)}")
    if features.quality.contradiction_flags:
        lines.append(f"- **Contradictions:** {', '.join(features.quality.contradiction_flags)}")
    lines.append("")

    # Debug (structured)
    if investigation.analysis.debug is not None and investigation.analysis.debug.promql:
        lines.append("### Debug: PromQL")
        lines.append("")
        for k, q in sorted(investigation.analysis.debug.promql.items()):
            lines.append(f"- **{k}:**")
            lines.append("")
            lines.append("```")
            lines.append(q)
            lines.append("```")
        lines.append("")

    # Noise details
    if investigation.analysis.noise is not None:
        lines.append("### Noise (structured)")
        lines.append("")
        n = investigation.analysis.noise
        if n.flap is not None:
            lines.append(f"- **flap.lookback:** `{n.flap.lookback}`")
            lines.append(f"- **flap.flaps_estimate:** {n.flap.flaps_estimate}")
            lines.append(f"- **flap.flap_score_0_100:** {n.flap.flap_score_0_100}")
        if n.cardinality is not None:
            if n.cardinality.ephemeral_labels_present:
                lines.append(
                    f"- **cardinality.ephemeral_labels_present:** {', '.join(n.cardinality.ephemeral_labels_present)}"
                )
            if n.cardinality.recommended_group_by:
                lines.append(f"- **cardinality.recommended_group_by:** {', '.join(n.cardinality.recommended_group_by)}")
            if n.cardinality.recommended_drop_labels:
                lines.append(
                    f"- **cardinality.recommended_drop_labels:** {', '.join(n.cardinality.recommended_drop_labels)}"
                )
        if n.missing_labels is not None and n.missing_labels.missing:
            lines.append(f"- **missing_labels.missing:** {', '.join(n.missing_labels.missing)}")
            if n.missing_labels.recommendation:
                lines.append(f"- **missing_labels.recommendation:** {' | '.join(n.missing_labels.recommendation[:3])}")
        lines.append("")

    # Capacity / rightsizing (structured)
    if investigation.analysis.capacity is not None:
        cap = investigation.analysis.capacity
        recs = cap.recommendations or []
        if recs:
            lines.append("### Capacity / Rightsizing")
            lines.append("")
            for r in recs[:5]:
                lines.append(f"- {r}")
            lines.append("")

    # K8s evidence summary
    lines.append("### Kubernetes")
    lines.append("")
    pi = investigation.evidence.k8s.pod_info or {}
    if isinstance(pi, dict):
        lines.append(f"- **Phase:** {pi.get('phase')}")
        lines.append(f"- **Node:** {pi.get('node_name')}")
    # Root-cause oriented K8s signals (compact; best-effort)
    if features is not None:
        kf = features.k8s
        if kf.status_reason or kf.status_message:
            bits = []
            if kf.status_reason:
                bits.append(str(kf.status_reason))
            if kf.status_message:
                bits.append(str(kf.status_message))
            lines.append(f"- **Pod status:** {' — '.join(bits)}")
        if kf.not_ready_conditions:
            lines.append("- **Not-ready conditions:**")
            for c in kf.not_ready_conditions[:6]:
                tail = f" (reason={c.reason})" if c.reason else ""
                lines.append(f"  - {c.type}={c.status}{tail}")
        if kf.container_waiting_reasons_top:
            lines.append("- **Container waiting:**")
            for w in kf.container_waiting_reasons_top[:3]:
                msg = f" — {w.message}" if w.message else ""
                lines.append(f"  - {w.container}: {w.reason or 'waiting'}{msg}")
        if kf.container_last_terminated_top:
            lines.append("- **Container last terminated:**")
            for t in kf.container_last_terminated_top[:3]:
                bits = []
                if t.reason:
                    bits.append(str(t.reason))
                if t.exit_code is not None:
                    bits.append(f"exitCode={t.exit_code}")
                lines.append(f"  - {t.container}: {', '.join(bits) if bits else 'terminated'}")
        if kf.recent_event_reasons_top:
            lines.append("- **Top events:**")
            for ev in kf.recent_event_reasons_top[:5]:
                cnt = f" x{ev.count}" if ev.count is not None else ""
                msg = f": {ev.message}" if ev.message else ""
                lines.append(f"  - {ev.reason or 'Event'}{cnt} ({ev.type or 'n/a'}){msg}")
    # Raw conditions (fallback) - avoid duplicating the derived summaries above
    if investigation.evidence.k8s.pod_conditions and not (features is not None and features.k8s.not_ready_conditions):
        lines.append("- **Conditions (non-True / scheduled):**")
        for c in investigation.evidence.k8s.pod_conditions[:10]:
            if not isinstance(c, dict):
                continue
            t = c.get("type")
            s = c.get("status")
            if t == "PodScheduled" or (s and s != "True"):
                lines.append(f"  - {t}: status={s}, reason={c.get('reason')}")
    lines.append("")

    # Metrics evidence summary
    lines.append("### Metrics")
    lines.append("")
    if features is not None:
        if features.metrics.cpu_throttle_p95_pct is not None:
            lines.append(f"- **cpu_throttle_p95_pct:** {features.metrics.cpu_throttle_p95_pct:.2f}")
        if features.metrics.cpu_usage_p95_cores is not None:
            lines.append(f"- **cpu_usage_p95_cores:** {features.metrics.cpu_usage_p95_cores:.3f}")
        if features.metrics.cpu_limit_cores is not None:
            lines.append(f"- **cpu_limit_cores:** {features.metrics.cpu_limit_cores:.3f}")
        if features.metrics.cpu_near_limit is not None:
            lines.append(f"- **cpu_near_limit:** {features.metrics.cpu_near_limit}")
        if features.k8s.restart_rate_5m_max is not None:
            lines.append(f"- **restart_rate_5m_max:** {features.k8s.restart_rate_5m_max:.2f}")
    lines.append("")

    # Logs summary
    lines.append("### Logs")
    lines.append("")
    lines.append(f"- **Status:** `{investigation.evidence.logs.logs_status or 'unknown'}`")
    if investigation.evidence.logs.logs_reason:
        lines.append(f"- **Reason:** `{investigation.evidence.logs.logs_reason}`")
    if investigation.evidence.logs.logs_backend:
        lines.append(f"- **Backend:** `{investigation.evidence.logs.logs_backend}`")
    if investigation.evidence.logs.logs_query:
        lines.append(f"- **Selector:** `{investigation.evidence.logs.logs_query}`")
    if investigation.evidence.logs.logs:
        lines.append(f"- **Entries:** {len(investigation.evidence.logs.logs)}")
        # Show a tiny actionable snippet (avoid startup banners dominating crashloop reports)
        snippet = select_snippet_latest_error_with_context(investigation.evidence.logs.logs, max_lines=12)
        if snippet:
            lines.append(f"- **Shown:** {len(snippet)} (prioritized errors; otherwise tail)")
        else:
            lines.append(
                "- **Shown:** 0 (all collected lines looked like startup noise; try expanding the time window)"
            )
        lines.append("")
        lines.append("```")
        for ln in snippet:
            lines.append(str(ln)[:240])
        lines.append("```")
    lines.append("")

    # AWS infrastructure evidence (if available)
    if investigation.evidence.aws and (
        investigation.evidence.aws.ec2_instances
        or investigation.evidence.aws.ebs_volumes
        or investigation.evidence.aws.elb_health
        or investigation.evidence.aws.rds_instances
        or investigation.evidence.aws.ecr_images
        or investigation.evidence.aws.networking
    ):
        lines.append("### AWS")
        lines.append("")

        # Extract metadata for display
        aws_metadata = investigation.evidence.aws.metadata or {}
        if aws_metadata.get("region"):
            lines.append(f"- **Region:** `{aws_metadata['region']}`")

        # EC2 instances
        if investigation.evidence.aws.ec2_instances:
            lines.append("")
            lines.append("**EC2 Instances:**")
            for instance_id, data in investigation.evidence.aws.ec2_instances.items():
                if isinstance(data, dict) and not data.get("error"):
                    state = data.get("state", "unknown")
                    system_status = data.get("system_status", "unknown")
                    instance_status = data.get("instance_status", "unknown")
                    status_emoji = "✅" if system_status == "ok" and instance_status == "ok" else "⚠️"
                    lines.append(
                        f"- {status_emoji} **{instance_id}:** state={state}, system={system_status}, instance={instance_status}"
                    )
                    # Show scheduled events if any
                    if data.get("scheduled_events"):
                        for event in data["scheduled_events"][:2]:  # Show max 2 events
                            lines.append(f"  - Scheduled: {event.get('code')} at {event.get('not_before')}")
                elif isinstance(data, dict) and data.get("error"):
                    lines.append(f"- ❌ **{instance_id}:** {data['error']}")

        # EBS volumes
        if investigation.evidence.aws.ebs_volumes:
            lines.append("")
            lines.append("**EBS Volumes:**")
            for volume_id, data in investigation.evidence.aws.ebs_volumes.items():
                if isinstance(data, dict) and not data.get("error"):
                    status = data.get("status", "unknown")
                    volume_type = data.get("volume_type", "unknown")
                    iops = data.get("iops", "N/A")
                    status_emoji = "✅" if status == "ok" else "⚠️"
                    lines.append(f"- {status_emoji} **{volume_id}:** status={status}, type={volume_type}, iops={iops}")
                    # Show throttling warnings
                    if data.get("performance_warnings"):
                        for warning in data["performance_warnings"][:2]:
                            lines.append(f"  - ⚠️ {warning}")
                elif isinstance(data, dict) and data.get("error"):
                    lines.append(f"- ❌ **{volume_id}:** {data['error']}")

        # ELB health
        if investigation.evidence.aws.elb_health:
            lines.append("")
            lines.append("**Load Balancer Health:**")
            for lb_name, data in investigation.evidence.aws.elb_health.items():
                if isinstance(data, dict) and not data.get("error"):
                    targets = data.get("targets", [])
                    healthy_count = sum(1 for t in targets if t.get("health") == "healthy")
                    total_count = len(targets)
                    health_emoji = "✅" if healthy_count == total_count else "⚠️"
                    lines.append(f"- {health_emoji} **{lb_name}:** {healthy_count}/{total_count} targets healthy")
                    # Show unhealthy targets
                    unhealthy = [t for t in targets if t.get("health") != "healthy"]
                    for target in unhealthy[:3]:  # Show max 3 unhealthy targets
                        target_id = target.get("target_id", "unknown")
                        reason = target.get("reason", "unknown")
                        lines.append(f"  - ⚠️ {target_id}: {reason}")
                elif isinstance(data, dict) and data.get("error"):
                    lines.append(f"- ❌ **{lb_name}:** {data['error']}")

        # RDS instances
        if investigation.evidence.aws.rds_instances:
            lines.append("")
            lines.append("**RDS Instances:**")
            for db_id, data in investigation.evidence.aws.rds_instances.items():
                if isinstance(data, dict) and not data.get("error"):
                    status = data.get("status", "unknown")
                    engine = data.get("engine", "unknown")
                    status_emoji = "✅" if status == "available" else "⚠️"
                    lines.append(f"- {status_emoji} **{db_id}:** status={status}, engine={engine}")
                    # Show maintenance events
                    if data.get("pending_maintenance"):
                        for event in data["pending_maintenance"][:2]:
                            lines.append(f"  - Maintenance: {event.get('action')} at {event.get('date')}")
                elif isinstance(data, dict) and data.get("error"):
                    lines.append(f"- ❌ **{db_id}:** {data['error']}")

        lines.append("")

    # CloudTrail infrastructure changes (if available)
    if investigation.evidence.aws and investigation.evidence.aws.cloudtrail_grouped:
        lines.append("### CloudTrail / Infrastructure Changes")
        lines.append("")

        grouped = investigation.evidence.aws.cloudtrail_grouped
        metadata = investigation.evidence.aws.cloudtrail_metadata or {}

        # Metadata line
        event_count = metadata.get("event_count", 0)
        lines.append(f"**Query**: {event_count} management events in time window")
        lines.append("")

        # Category order (priority)
        category_order = [
            "security_group",
            "auto_scaling",
            "ec2_lifecycle",
            "iam_policy",
            "storage",
            "database",
            "networking",
            "load_balancer",
        ]

        category_labels = {
            "security_group": "Security Group Changes",
            "auto_scaling": "Auto Scaling",
            "ec2_lifecycle": "EC2 Lifecycle",
            "iam_policy": "IAM Policy Changes",
            "storage": "Storage (EBS)",
            "database": "Database (RDS)",
            "networking": "Networking",
            "load_balancer": "Load Balancer",
        }

        for category in category_order:
            if category not in grouped:
                continue

            events = grouped[category]
            lines.append(f"**{category_labels[category]}** ({len(events)} events):")

            for event in events[:5]:  # Limit to 5 per category for readability
                event_name = event.get("EventName", "Unknown")
                event_time = event.get("EventTime", "")
                username = event.get("Username", "unknown")

                # Format time as relative ("5m ago")
                if event_time:
                    try:
                        event_dt = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
                        now = datetime.now(timezone.utc)
                        delta = now - event_dt
                        if delta.total_seconds() < 3600:
                            time_str = f"{int(delta.total_seconds() / 60)}m ago"
                        else:
                            time_str = f"{int(delta.total_seconds() / 3600)}h ago"
                    except Exception:
                        time_str = "unknown"
                else:
                    time_str = "unknown"

                # Visual indicator based on category
                if category in ["security_group", "iam_policy"]:
                    emoji = "🔒"  # Security-related
                elif category in ["auto_scaling", "ec2_lifecycle"]:
                    emoji = "⚙️"  # Infrastructure changes
                elif category in ["storage", "database"]:
                    emoji = "💾"  # Data-related
                else:
                    emoji = "🔧"  # Other

                lines.append(f"- {emoji} **{event_name}** by {username} ({time_str})")

            if len(events) > 5:
                lines.append(f"  ... and {len(events) - 5} more")

            lines.append("")

    # GitHub code change evidence (if available)
    if investigation.evidence.github and investigation.evidence.github.repo:
        lines.append("### GitHub / Changes")
        lines.append("")

        # Repository info
        repo = investigation.evidence.github.repo
        discovery_method = investigation.evidence.github.repo_discovery_method or "unknown"
        is_third_party = investigation.evidence.github.is_third_party
        third_party_note = " (third-party)" if is_third_party else ""
        lines.append(f"**Repository:** `{repo}`{third_party_note} (discovered via: {discovery_method})")
        lines.append("")

        # Recent commits
        if investigation.evidence.github.recent_commits:
            lines.append("**Recent Commits** (time window before alert):")
            for commit in investigation.evidence.github.recent_commits[:5]:  # Show max 5
                sha = commit.get("sha", "unknown")[:7]
                author = commit.get("author", "unknown")
                message = commit.get("message", "").split("\n")[0][:80]  # First line, truncated
                timestamp = commit.get("timestamp", "")
                lines.append(f"- `{sha}` by {author}: {message}")
                if timestamp:
                    lines.append(f"  - {timestamp}")
            lines.append("")

        # Workflow runs
        if investigation.evidence.github.workflow_runs:
            lines.append("**Recent Builds:**")
            for run in investigation.evidence.github.workflow_runs[:5]:  # Show max 5
                workflow_name = run.get("workflow_name", "unknown")
                conclusion = run.get("conclusion", "unknown")
                status = run.get("status", "unknown")
                run_id = run.get("id", "")
                created_at = run.get("created_at", "")

                # Emoji based on conclusion
                if conclusion == "success":
                    emoji = "✅"
                elif conclusion == "failure":
                    emoji = "❌"
                elif conclusion == "cancelled":
                    emoji = "🚫"
                else:
                    emoji = "⏳"

                lines.append(f"- {emoji} Workflow `{workflow_name}` #{run_id}: {status}/{conclusion}")
                if created_at:
                    lines.append(f"  - {created_at}")

                # Show failed jobs
                if conclusion == "failure" and run.get("jobs"):
                    failed_jobs = [j for j in run.get("jobs", []) if j.get("conclusion") == "failure"]
                    for job in failed_jobs[:2]:  # Show max 2 failed jobs
                        job_name = job.get("name", "unknown")
                        lines.append(f"  - Failed job: `{job_name}`")

            lines.append("")

        # Failed workflow logs (if any)
        if investigation.evidence.github.failed_workflow_logs:
            lines.append("**Failed Workflow Logs** (snippet):")
            lines.append("```")
            log_lines = investigation.evidence.github.failed_workflow_logs.split("\n")
            for line in log_lines[:20]:  # Show first 20 lines
                lines.append(line[:240])  # Truncate long lines
            if len(log_lines) > 20:
                lines.append(f"... ({len(log_lines) - 20} more lines)")
            lines.append("```")
            lines.append("")

        # Documentation availability
        if investigation.evidence.github.readme or investigation.evidence.github.docs:
            lines.append("**Documentation:**")
            if investigation.evidence.github.readme:
                lines.append("- README.md available")
            if investigation.evidence.github.docs:
                for doc in investigation.evidence.github.docs[:3]:  # Show max 3 docs
                    doc_path = doc.get("path", "unknown")
                    lines.append(f"- {doc_path} available")
            lines.append("")

    return "\n".join(lines)


#
# NOTE: log selection lives in `agent.logs_select` (shared by report + scoring)
#
