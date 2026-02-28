"""Unit tests for crashloop family enrichment."""

from datetime import datetime, timezone

from agent.core.models import (
    DerivedFeatures,
    Evidence,
    FeaturesK8s,
    FeaturesLogs,
    Investigation,
    K8sContainerLastTerminated,
    K8sContainerWaiting,
    K8sEventSummary,
    LogsEvidence,
    TargetRef,
    TimeWindow,
)
from agent.pipeline.enrich import build_family_enrichment


def _make_investigation(**kwargs) -> Investigation:
    now = datetime.now(timezone.utc)
    defaults = {
        "alert": {"labels": {"alertname": "KubePodCrashLooping"}},
        "time_window": TimeWindow(window="1h", start_time=now, end_time=now),
        "target": TargetRef(namespace="default", pod="my-app-abc123", container="my-app"),
        "evidence": Evidence(),
    }
    defaults.update(kwargs)
    return Investigation(**defaults)


class TestCrashloopEnrichment:
    """Test _enrich_crashloop via build_family_enrichment."""

    def _crashloop_inv(
        self,
        *,
        exit_code=1,
        exit_reason="Error",
        probe_type=None,
        crash_duration=None,
        waiting="CrashLoopBackOff",
        restart_rate=5.0,
        parsed_errors=None,
        **extra_meta
    ) -> Investigation:
        """Helper to build a crashloop investigation with features."""
        meta = {"family": "crashloop"}
        if probe_type is not None:
            meta["probe_failure_type"] = probe_type
        if crash_duration is not None:
            meta["crash_duration_seconds"] = crash_duration
        meta.update(extra_meta)

        inv = _make_investigation(meta=meta)
        inv.analysis.features = DerivedFeatures(
            family="crashloop",
            k8s=FeaturesK8s(
                pod_phase="Running",
                ready=False,
                waiting_reason=waiting,
                restart_rate_5m_max=restart_rate,
                container_waiting_reasons_top=[
                    K8sContainerWaiting(container="my-app", reason=waiting),
                ],
                container_last_terminated_top=[
                    K8sContainerLastTerminated(container="my-app", reason=exit_reason, exit_code=exit_code),
                ],
                recent_event_reasons_top=[
                    K8sEventSummary(
                        reason="BackOff", count=15, type="Warning", message="Back-off restarting failed container"
                    ),
                ],
                warning_events_count=15,
            ),
            logs=FeaturesLogs(status="ok"),
        )
        if parsed_errors:
            inv.evidence.logs = LogsEvidence(parsed_errors=parsed_errors)
        return inv

    def test_returns_decision_for_crashloop(self):
        """Test that enrichment returns a Decision for crashloop family."""
        inv = self._crashloop_inv()
        result = build_family_enrichment(inv)
        assert result is not None
        assert result.label is not None
        assert len(result.why) > 0
        assert len(result.next) > 0

    def test_label_suspected_oom_crash(self):
        """Exit 137 / OOMKilled → suspected_oom_crash."""
        inv = self._crashloop_inv(exit_code=137, exit_reason="OOMKilled")
        result = build_family_enrichment(inv)
        assert result.label == "suspected_oom_crash"

    def test_label_suspected_liveness_probe_failure(self):
        """Exit 0 + liveness probe → suspected_liveness_probe_failure."""
        inv = self._crashloop_inv(exit_code=0, exit_reason="Completed", probe_type="liveness")
        result = build_family_enrichment(inv)
        assert result.label == "suspected_liveness_probe_failure"

    def test_label_suspected_dependency_unavailable(self):
        """Connection refused in logs → suspected_dependency_unavailable."""
        inv = self._crashloop_inv(parsed_errors=[{"message": "ECONNREFUSED 10.0.0.1:6379"}])
        result = build_family_enrichment(inv)
        assert result.label == "suspected_dependency_unavailable"

    def test_label_suspected_config_or_permission_error(self):
        """FileNotFoundError in logs → suspected_config_or_permission_error."""
        inv = self._crashloop_inv(parsed_errors=[{"message": "FileNotFoundError: /etc/config/app.yaml"}])
        result = build_family_enrichment(inv)
        assert result.label == "suspected_config_or_permission_error"

    def test_label_suspected_app_startup_failure(self):
        """Exit 1 + crash_duration < 10s → suspected_app_startup_failure."""
        inv = self._crashloop_inv(exit_code=1, crash_duration=3)
        result = build_family_enrichment(inv)
        assert result.label == "suspected_app_startup_failure"

    def test_label_suspected_app_runtime_failure(self):
        """Exit 1 + crash_duration > 60s → suspected_app_runtime_failure."""
        inv = self._crashloop_inv(exit_code=1, crash_duration=120)
        result = build_family_enrichment(inv)
        assert result.label == "suspected_app_runtime_failure"

    def test_label_unknown_when_no_signals(self):
        """No distinguishing signals → unknown_needs_human."""
        inv = self._crashloop_inv(exit_code=2)
        result = build_family_enrichment(inv)
        assert result.label == "unknown_needs_human"

    def test_why_includes_pod_status(self):
        """Why bullets should include pod status."""
        inv = self._crashloop_inv()
        result = build_family_enrichment(inv)
        assert any("Pod status" in w for w in result.why)

    def test_why_includes_restart_rate(self):
        """Why bullets should include restart rate."""
        inv = self._crashloop_inv(restart_rate=10.0)
        result = build_family_enrichment(inv)
        assert any("restart" in w.lower() for w in result.why)

    def test_why_includes_crash_duration(self):
        """Why bullets should include crash duration when available."""
        inv = self._crashloop_inv(crash_duration=5)
        result = build_family_enrichment(inv)
        assert any("crash duration" in w.lower() for w in result.why)

    def test_why_includes_probe_failure(self):
        """Why bullets should include probe failure type."""
        inv = self._crashloop_inv(probe_type="liveness")
        result = build_family_enrichment(inv)
        assert any("probe" in w.lower() for w in result.why)

    def test_next_steps_contain_promql(self):
        """Next steps should include PromQL queries."""
        inv = self._crashloop_inv()
        result = build_family_enrichment(inv)
        assert any("kube_pod_container_status_restarts_total" in n for n in result.next)

    def test_next_steps_contain_kubectl_logs(self):
        """Next steps should include kubectl logs --previous."""
        inv = self._crashloop_inv()
        result = build_family_enrichment(inv)
        assert any("--previous" in n for n in result.next)

    def test_missing_k8s_context_still_returns_decision(self):
        """Should still return a decision when K8s context is missing."""
        inv = _make_investigation(meta={"family": "crashloop"})
        inv.analysis.features = DerivedFeatures(
            family="crashloop",
            k8s=FeaturesK8s(),
        )
        result = build_family_enrichment(inv)
        assert result is not None

    def test_missing_pod_target_provides_scenario_a(self):
        """Without pod target, next steps should point to Scenario A."""
        inv = _make_investigation(
            meta={"family": "crashloop"},
            target=TargetRef(namespace=None, pod=None),
        )
        inv.analysis.features = DerivedFeatures(
            family="crashloop",
            k8s=FeaturesK8s(),
        )
        result = build_family_enrichment(inv)
        assert any("Scenario A" in n for n in result.next)


class TestFamilyDetection:
    """Test that crashloop family is detected correctly."""

    def test_detect_crashloop_from_alertname(self):
        from agent.pipeline.families import detect_family

        assert detect_family({"alertname": "KubePodCrashLooping"}, None) == "crashloop"

    def test_detect_crashloop_from_playbook(self):
        from agent.pipeline.families import detect_family

        assert detect_family({"alertname": "SomeAlert"}, "crashloop") == "crashloop"

    def test_detect_crashloop_strict_variant(self):
        from agent.pipeline.families import detect_family

        assert detect_family({"alertname": "KubePodCrashLoopingStrict"}, None) == "crashloop"
