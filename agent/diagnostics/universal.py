from __future__ import annotations

from typing import List

from agent.core.family import get_family
from agent.core.models import Hypothesis, Investigation
from agent.diagnostics.crashloop_diagnostics import CrashLoopDiagnosticModule
from agent.diagnostics.job_diagnostics import JobFailureDiagnosticModule


def _family(investigation: Investigation) -> str:
    return get_family(investigation, default="unknown")


def _clamp_0_100(x: int) -> int:
    return max(0, min(100, int(x)))


class K8sLifecycleModule:
    module_id = "k8s_lifecycle"

    def applies(self, investigation: Investigation) -> bool:
        return _family(investigation) in {"pod_not_healthy"}

    def collect(self, investigation: Investigation) -> None:
        fam = _family(investigation)
        if fam == "pod_not_healthy":
            from agent.collectors.pod_not_healthy import collect_pod_not_healthy

            collect_pod_not_healthy(investigation)
            return
        from agent.collectors.pod_baseline import collect_pod_baseline

        collect_pod_baseline(investigation, events_limit=20)

    def diagnose(self, investigation: Investigation) -> List[Hypothesis]:
        f = investigation.analysis.features
        out: List[Hypothesis] = []
        if f is None:
            return out

        # Pod health: prefer concrete K8s reasons.
        waiting = (f.k8s.waiting_reason or "").strip()
        if waiting in {"ImagePullBackOff", "ErrImagePull"}:
            out.append(
                Hypothesis(
                    hypothesis_id="image_pull_failure",
                    title="Image pull failure (auth/not found/network)",
                    confidence_0_100=_clamp_0_100(80),
                    why=[f"Container waiting reason is `{waiting}`."],
                    supporting_refs=["features.k8s.waiting_reason", "k8s.pod_events"],
                    next_tests=[
                        "kubectl -n <ns> describe pod <pod>",
                        "Check node egress/DNS to registry and imagePullSecrets/IAM wiring.",
                    ],
                )
            )
        if waiting in {"CreateContainerConfigError", "CreateContainerError"}:
            out.append(
                Hypothesis(
                    hypothesis_id="misconfig_or_missing_secret_configmap",
                    title="Misconfiguration or missing Secret/ConfigMap",
                    confidence_0_100=_clamp_0_100(75),
                    why=[f"Container waiting reason is `{waiting}`."],
                    supporting_refs=["features.k8s.waiting_reason", "k8s.pod_events"],
                    next_tests=[
                        "kubectl -n <ns> describe pod <pod>  # look for missing keys/resources",
                        "kubectl -n <ns> get cm,secret | grep -i <name>",
                    ],
                )
            )
        if waiting == "CrashLoopBackOff":
            score = 55
            if (f.k8s.restart_rate_5m_max or 0) >= 3:
                score += 15
            if f.logs.status == "ok":
                score += 10
            out.append(
                Hypothesis(
                    hypothesis_id="crashloop_app_failure",
                    title="Application crash / startup failure (CrashLoopBackOff)",
                    confidence_0_100=_clamp_0_100(score),
                    why=[
                        "CrashLoop signals present (waiting reason and/or restart rate).",
                        "Use logs and last termination details to determine the immediate cause.",
                    ],
                    supporting_refs=["features.k8s.waiting_reason", "features.k8s.restart_rate_5m_max", "logs.logs"],
                    next_tests=[
                        "kubectl -n <ns> logs <pod> -c <container> --previous --tail=200",
                        "kubectl -n <ns> describe pod <pod>  # events + lastState.terminated",
                    ],
                )
            )
        return out


class RolloutHealthModule:
    module_id = "rollout_health"

    def applies(self, investigation: Investigation) -> bool:
        return _family(investigation) == "k8s_rollout_health"

    def collect(self, investigation: Investigation) -> None:
        from agent.collectors.nonpod_baseline import collect_nonpod_baseline

        collect_nonpod_baseline(investigation)

    def diagnose(self, investigation: Investigation) -> List[Hypothesis]:
        f = investigation.analysis.features
        if f is None:
            return []
        # If change correlation suggests near change, highlight regression possibility.
        ch = investigation.analysis.change
        score = 40
        why: List[str] = ["Workload health/rollout alert fired."]
        if ch is not None and ch.score is not None:
            score += int(50 * float(ch.score))
            why.append(ch.summary or "Change correlation computed.")
        return [
            Hypothesis(
                hypothesis_id="rollout_blocked_or_regression",
                title="Rollout blocked or workload regression",
                confidence_0_100=_clamp_0_100(score),
                why=why[:4],
                supporting_refs=["k8s.rollout_status", "analysis.change"],
                next_tests=[
                    "kubectl -n <ns> rollout status deploy/<name>",
                    "kubectl -n <ns> describe deploy/<name>  # conditions/events",
                ],
            )
        ]


class CapacityModule:
    module_id = "capacity"

    def applies(self, investigation: Investigation) -> bool:
        return _family(investigation) in {"cpu_throttling", "oom_killed", "memory_pressure"}

    def collect(self, investigation: Investigation) -> None:
        fam = _family(investigation)
        if fam == "cpu_throttling":
            from agent.collectors.cpu_throttling import collect_cpu_throttling

            collect_cpu_throttling(investigation)
            return
        if fam == "oom_killed":
            from agent.collectors.oom_killer import collect_oom_killer

            collect_oom_killer(investigation)
            return
        if fam == "memory_pressure":
            from agent.collectors.memory_pressure import collect_memory_pressure

            collect_memory_pressure(investigation)
            return

    def diagnose(self, investigation: Investigation) -> List[Hypothesis]:
        f = investigation.analysis.features
        if f is None:
            return []
        fam = _family(investigation)
        out: List[Hypothesis] = []
        if fam == "cpu_throttling":
            near = f.metrics.cpu_near_limit is True
            score = 70 if near else 35
            out.append(
                Hypothesis(
                    hypothesis_id="cpu_capacity_limit",
                    title="CPU capacity/limits causing throttling (only actionable when near limit)",
                    confidence_0_100=_clamp_0_100(score),
                    why=[
                        f"cpu_throttle_p95_pct={f.metrics.cpu_throttle_p95_pct} (near_limit={near})",
                        "If usage is far from limit, raising limits is unlikely to help; investigate per-container throttling and impact signals.",
                    ],
                    supporting_refs=["features.metrics.cpu_throttle_p95_pct", "features.metrics.cpu_near_limit"],
                    next_tests=[
                        "PromQL: per-container throttling topk (see debug promql if present).",
                        "Correlate with latency/errors in the same window to determine impact.",
                    ],
                )
            )
        if fam == "oom_killed":
            score = 80 if f.k8s.oom_killed else 40
            out.append(
                Hypothesis(
                    hypothesis_id="memory_limit_oom",
                    title="Container exceeded memory limit (OOMKilled)",
                    confidence_0_100=_clamp_0_100(score),
                    why=[
                        (
                            "OOMKilled evidence present."
                            if f.k8s.oom_killed
                            else "OOM alert fired but lacks K8s corroboration."
                        )
                    ],
                    supporting_refs=["features.k8s.oom_killed", "features.metrics.memory_usage_p95_bytes"],
                    next_tests=[
                        "Check memory usage vs limits/requests; consider increasing limit if justified.",
                        "Look for allocation spikes/leaks around the window (logs/app metrics).",
                    ],
                )
            )
        if fam == "memory_pressure":
            score = 70 if f.metrics.memory_near_limit is True else 40
            out.append(
                Hypothesis(
                    hypothesis_id="memory_pressure",
                    title="Memory pressure / eviction risk",
                    confidence_0_100=_clamp_0_100(score),
                    why=[
                        (
                            "Memory is near limit."
                            if f.metrics.memory_near_limit
                            else "Memory pressure signals detected but not near limit."
                        )
                    ],
                    supporting_refs=["features.metrics.memory_near_limit", "features.k8s.evicted"],
                    next_tests=[
                        "Check eviction/node pressure signals and container memory usage trends.",
                        "Verify recent changes that may increase memory footprint.",
                    ],
                )
            )
        return out


class DataPlaneModule:
    module_id = "data_plane"

    def applies(self, investigation: Investigation) -> bool:
        return _family(investigation) == "http_5xx"

    def collect(self, investigation: Investigation) -> None:
        from agent.collectors.http_5xx import collect_http_5xx

        collect_http_5xx(investigation)

    def diagnose(self, investigation: Investigation) -> List[Hypothesis]:
        f = investigation.analysis.features
        if f is None:
            return []
        score = 50
        if (f.metrics.http_5xx_rate_p95 or 0) >= 1.0:
            score += 25
        if investigation.analysis.change and (investigation.analysis.change.score or 0) >= 0.5:
            score += 10
        return [
            Hypothesis(
                hypothesis_id="upstream_or_regression",
                title="Upstream dependency issue or recent regression causing 5xx",
                confidence_0_100=_clamp_0_100(score),
                why=["5xx rate elevated; correlate with dependency timeouts and recent changes."],
                supporting_refs=["features.metrics.http_5xx_rate_p95", "analysis.change", "logs.logs"],
                next_tests=[
                    "Correlate 5xx with latency/timeouts and upstream service health.",
                    "If there was a recent rollout near onset, consider rollback after confirming impact.",
                ],
            )
        ]


class ControlPlaneModule:
    module_id = "control_plane"

    def applies(self, investigation: Investigation) -> bool:
        return _family(investigation) == "target_down"

    def collect(self, investigation: Investigation) -> None:
        from agent.collectors.nonpod_baseline import collect_nonpod_baseline

        collect_nonpod_baseline(investigation)

    def diagnose(self, investigation: Investigation) -> List[Hypothesis]:
        return [
            Hypothesis(
                hypothesis_id="scrape_target_unreachable",
                title="Scrape target unreachable (network/DNS/exporter down) or label mismatch",
                confidence_0_100=50,
                why=["TargetDown-style symptoms; verify /targets and scrape errors."],
                supporting_refs=["noise.prometheus", "alert.labels.instance", "alert.labels.job"],
                next_tests=[
                    "Check Prometheus /targets for scrape errors and last scrape time.",
                    "Verify DNS/network/TLS to the target and exporter process health.",
                ],
            )
        ]


class ObservabilityPipelineModule:
    module_id = "observability_pipeline"

    def applies(self, investigation: Investigation) -> bool:
        return _family(investigation) == "observability_pipeline" or _family(investigation) == "meta"

    def collect(self, investigation: Investigation) -> None:
        fam = _family(investigation)
        if fam == "meta":
            return
        from agent.collectors.nonpod_baseline import collect_nonpod_baseline

        collect_nonpod_baseline(investigation)

    def diagnose(self, investigation: Investigation) -> List[Hypothesis]:
        fam = _family(investigation)
        if fam == "meta":
            return [
                Hypothesis(
                    hypothesis_id="meta_alert",
                    title="Meta/inhibitor alert (operational noise)",
                    confidence_0_100=90,
                    why=["This alert suppresses others; it is not a direct incident symptom."],
                    supporting_refs=["alert.labels.alertname"],
                    next_tests=["Review Alertmanager inhibition rules and routing to reduce paging noise."],
                )
            ]
        return [
            Hypothesis(
                hypothesis_id="obs_pipeline_degraded",
                title="Observability pipeline degraded (rules/ingestion/backpressure)",
                confidence_0_100=70,
                why=["Observability pipeline family alert fired."],
                supporting_refs=["analysis.noise", "alert.labels"],
                next_tests=[
                    "Check vmalert/vminsert/logs backend for errors and saturation.",
                    "Review recent rule/config changes and ingestion rejects.",
                ],
            )
        ]


# Explicit default module set (used by registry; keeps composition in one place).
DEFAULT_MODULE_CLASSES = [
    CrashLoopDiagnosticModule,  # Crashloop (exit codes + probe failures + log pattern matching)
    JobFailureDiagnosticModule,  # Job failures (uses log pattern matching framework)
    K8sLifecycleModule,
    RolloutHealthModule,
    CapacityModule,
    DataPlaneModule,
    ControlPlaneModule,
    ObservabilityPipelineModule,
]
