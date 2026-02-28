"""Diagnostic module for CrashLoopBackOff failures.

Uses a layered approach:
1. Exit code differentiation (from K8s container status)
2. Probe failure detection (from pod events)
3. Log pattern matching (reuses generic framework from job_diagnostics)
4. Fallback generic hypothesis

Follows the same structure as JobFailureDiagnosticModule.
"""

from collections import defaultdict
from typing import List

from agent.core.family import get_family
from agent.core.models import Hypothesis, Investigation
from agent.diagnostics.base import DiagnosticModule
from agent.diagnostics.log_pattern_matcher import LogPatternMatcher
from agent.diagnostics.patterns.crashloop_patterns import CRASHLOOP_PATTERNS


class CrashLoopDiagnosticModule(DiagnosticModule):
    """Diagnose CrashLoopBackOff by combining exit codes, probe failures, and log patterns.

    Layered diagnosis:
    - Layer 1: Exit code differentiation (137=OOM, 139=segfault, 1=app error, 0=probe)
    - Layer 2: Probe failure from events (liveness/readiness)
    - Layer 3: Log pattern matching (dependency, config, OOM, permission, database)
    - Layer 4: Fallback generic hypothesis
    """

    module_id = "crashloop"

    def __init__(self):
        self.matcher = LogPatternMatcher(CRASHLOOP_PATTERNS)

    def applies(self, investigation: Investigation) -> bool:
        return get_family(investigation, default="") == "crashloop"

    def collect(self, investigation: Investigation) -> None:
        from agent.collectors.crashloop import collect_crashloop_evidence

        collect_crashloop_evidence(investigation)

    def diagnose(self, investigation: Investigation) -> List[Hypothesis]:
        hypotheses: List[Hypothesis] = []
        f = investigation.analysis.features

        # --- Layer 1: Exit code differentiation ---
        exit_code = None
        exit_reason = None
        if f is not None and f.k8s.container_last_terminated_top:
            term = f.k8s.container_last_terminated_top[0]
            exit_code = term.exit_code
            exit_reason = (term.reason or "").strip()

        if exit_code == 137 or (exit_reason and exit_reason.lower() == "oomkilled"):
            hypotheses.append(
                Hypothesis(
                    hypothesis_id="crashloop_oom",
                    title="Container OOMKilled (exit code 137)",
                    confidence_0_100=80,
                    why=[
                        f"Container terminated with exit code {exit_code} (reason={exit_reason or 'n/a'}).",
                        "Exit code 137 indicates the container was killed by the OOM killer.",
                    ],
                    supporting_refs=["features.k8s.container_last_terminated_top"],
                    next_tests=[
                        "Check memory limits vs actual usage:",
                        'max by (container) (kube_pod_container_resource_limits{namespace="<ns>",pod="<pod>",resource="memory"})',
                        'quantile_over_time(0.95, container_memory_working_set_bytes{namespace="<ns>",pod="<pod>",container!="POD",image!=""}[30m])',
                        "kubectl -n <ns> describe pod <pod>  # check lastState.terminated.reason",
                    ],
                )
            )

        elif exit_code == 139:
            hypotheses.append(
                Hypothesis(
                    hypothesis_id="crashloop_segfault",
                    title="Container segmentation fault (exit code 139)",
                    confidence_0_100=75,
                    why=[
                        "Container terminated with exit code 139 (SIGSEGV).",
                        "This usually indicates a memory corruption bug in native code.",
                    ],
                    supporting_refs=["features.k8s.container_last_terminated_top"],
                    next_tests=[
                        "kubectl -n <ns> logs <pod> -c <container> --previous --tail=200",
                        "Check if a recent image update introduced native library changes.",
                        "kubectl -n <ns> describe pod <pod>  # check image tag/digest",
                    ],
                )
            )

        elif exit_code == 0:
            # Exit 0 with restarts → likely liveness probe killing the container
            hypotheses.append(
                Hypothesis(
                    hypothesis_id="crashloop_liveness_probe",
                    title="Container exits cleanly but restarts (possible liveness probe kill)",
                    confidence_0_100=70,
                    why=[
                        "Container exited with code 0 (clean exit) but is restarting.",
                        "This often indicates a liveness probe is killing the container before it becomes ready.",
                    ],
                    supporting_refs=["features.k8s.container_last_terminated_top", "k8s.pod_events"],
                    next_tests=[
                        "kubectl -n <ns> describe pod <pod>  # check liveness probe config and events",
                        "Review liveness probe timeout/period settings — may need tuning for slow-starting apps.",
                    ],
                )
            )

        elif exit_code == 1:
            crash_duration = investigation.meta.get("crash_duration_seconds")
            if crash_duration is not None and crash_duration < 10:
                confidence = 65
                hint = "Instant crash (<10s) suggests config or dependency issue at startup."
            elif crash_duration is not None and crash_duration > 60:
                confidence = 60
                hint = "Slow crash (>60s) suggests runtime failure (timeout, memory leak, etc.)."
            else:
                confidence = 60
                hint = "Application error — check logs for the root cause."
            hypotheses.append(
                Hypothesis(
                    hypothesis_id="crashloop_app_error",
                    title="Application error (exit code 1)",
                    confidence_0_100=confidence,
                    why=[
                        "Container terminated with exit code 1 (application error).",
                        hint,
                    ],
                    supporting_refs=["features.k8s.container_last_terminated_top", "logs.logs"],
                    next_tests=[
                        "kubectl -n <ns> logs <pod> -c <container> --previous --tail=200",
                        "kubectl -n <ns> describe pod <pod>",
                    ],
                )
            )

        # --- Layer 2: Probe failure from events ---
        probe_type = investigation.meta.get("probe_failure_type")
        if probe_type == "liveness":
            # Only add if we don't already have a liveness hypothesis from exit code 0
            existing_ids = {h.hypothesis_id for h in hypotheses}
            if "crashloop_liveness_probe" not in existing_ids:
                hypotheses.append(
                    Hypothesis(
                        hypothesis_id="crashloop_liveness_probe_failure",
                        title="Liveness probe failing (container killed by kubelet)",
                        confidence_0_100=75,
                        why=[
                            "Liveness probe Unhealthy events detected in pod events.",
                            "Kubelet kills the container when liveness probe fails, causing CrashLoopBackOff.",
                        ],
                        supporting_refs=["k8s.pod_events", "meta.probe_failure_type"],
                        next_tests=[
                            "kubectl -n <ns> describe pod <pod>  # check liveness probe configuration",
                            "Review probe initialDelaySeconds — may be too short for slow-starting apps.",
                            "Check if the health endpoint is actually responding:",
                            "kubectl -n <ns> exec <pod> -- curl -s localhost:<port>/healthz",
                        ],
                    )
                )
        elif probe_type == "readiness":
            hypotheses.append(
                Hypothesis(
                    hypothesis_id="crashloop_readiness_probe_failure",
                    title="Readiness probe failing (container not receiving traffic)",
                    confidence_0_100=60,
                    why=[
                        "Readiness probe Unhealthy events detected in pod events.",
                        "Readiness failures alone don't cause restarts but indicate the app is not healthy.",
                    ],
                    supporting_refs=["k8s.pod_events", "meta.probe_failure_type"],
                    next_tests=[
                        "kubectl -n <ns> describe pod <pod>  # check readiness probe configuration",
                        "Check application startup time and whether readiness endpoint works.",
                    ],
                )
            )

        # --- Layer 3: Log pattern matching ---
        parsed_errors = None
        if investigation.evidence.logs and investigation.evidence.logs.parsed_errors:
            parsed_errors = investigation.evidence.logs.parsed_errors
        # Also check previous container parsed errors
        prev_parsed = investigation.meta.get("previous_logs_parsed_errors")

        # Combine both sources
        combined_errors = []
        if parsed_errors:
            combined_errors.extend(parsed_errors)
        if prev_parsed:
            combined_errors.extend(prev_parsed)

        if combined_errors:
            matches = self.matcher.find_matches(combined_errors)
            for pattern, context in matches:
                # Build context with investigation details
                full_context = defaultdict(
                    lambda: "unknown",
                    {
                        "namespace": investigation.target.namespace or "default",
                        "pod": investigation.target.pod or "unknown",
                    },
                )
                full_context.update(context)

                matching_count = sum(1 for e in combined_errors if pattern.matches(e.get("message", "")))

                try:
                    why_message = pattern.why_template.format_map(full_context)
                except (KeyError, ValueError):
                    why_message = pattern.title

                why_bullets = [
                    why_message,
                    f"Found {matching_count} matching error pattern(s) in logs",
                ]

                sample_error = next(
                    (e for e in combined_errors if pattern.matches(e.get("message", ""))),
                    None,
                )
                if sample_error:
                    why_bullets.append(f"Sample: {sample_error.get('message', '')[:200]}")

                combined_next = []
                if pattern.remediation_steps:
                    combined_next.extend([cmd.format_map(full_context) for cmd in pattern.remediation_steps])
                if pattern.next_tests:
                    if pattern.remediation_steps:
                        combined_next.append("")
                    combined_next.extend([cmd.format_map(full_context) for cmd in pattern.next_tests])

                # Resolve placeholders
                from agent.utils.placeholder_resolver import PlaceholderResolver

                resolver = PlaceholderResolver(investigation)
                resolved_next = [resolver.resolve(cmd) for cmd in combined_next]

                hypotheses.append(
                    Hypothesis(
                        hypothesis_id=pattern.pattern_id,
                        title=pattern.title,
                        confidence_0_100=pattern.confidence,
                        why=why_bullets,
                        supporting_refs=["evidence.logs.parsed_errors", "meta.previous_logs_parsed_errors"],
                        next_tests=resolved_next,
                    )
                )

        # --- Layer 4: Fallback generic hypothesis ---
        if not hypotheses:
            score = 55
            if f is not None and (f.k8s.restart_rate_5m_max or 0) >= 3:
                score += 15
            if f is not None and f.logs.status == "ok":
                score += 10
            hypotheses.append(
                Hypothesis(
                    hypothesis_id="crashloop_generic",
                    title="Application crash / startup failure (CrashLoopBackOff)",
                    confidence_0_100=max(0, min(100, score)),
                    why=[
                        "CrashLoop signals present (waiting reason and/or restart rate).",
                        "No specific error pattern matched — check logs for the root cause.",
                    ],
                    supporting_refs=["features.k8s.waiting_reason", "features.k8s.restart_rate_5m_max", "logs.logs"],
                    next_tests=[
                        "kubectl -n <ns> logs <pod> -c <container> --previous --tail=200",
                        "kubectl -n <ns> describe pod <pod>  # events + lastState.terminated",
                    ],
                )
            )

        return hypotheses
