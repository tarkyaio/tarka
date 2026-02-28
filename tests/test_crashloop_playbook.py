"""Unit tests for crashloop playbook and collector."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from agent.collectors.crashloop import collect_crashloop_evidence
from agent.core.models import (
    Evidence,
    Investigation,
    TargetRef,
    TimeWindow,
)


def _make_investigation(**kwargs) -> Investigation:
    """Create a test Investigation with sensible defaults."""
    now = datetime.now(timezone.utc)
    defaults = {
        "alert": {"labels": {"alertname": "KubePodCrashLooping", "namespace": "default", "pod": "my-app-abc123"}},
        "time_window": TimeWindow(window="1h", start_time=now, end_time=now),
        "target": TargetRef(namespace="default", pod="my-app-abc123", target_type="pod"),
        "evidence": Evidence(),
    }
    defaults.update(kwargs)
    return Investigation(**defaults)


def _mock_providers():
    """Return mock patches for all external providers."""
    return {
        "k8s_context": patch(
            "agent.collectors.pod_baseline.gather_pod_context",
            return_value={
                "pod_info": {
                    "name": "my-app-abc123",
                    "namespace": "default",
                    "phase": "Running",
                    "containers": [{"name": "my-app", "image": "my-app:v1.0"}],
                    "container_statuses": [
                        {
                            "name": "my-app",
                            "ready": False,
                            "restart_count": 5,
                            "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                            "last_state": {
                                "terminated": {
                                    "exit_code": 1,
                                    "reason": "Error",
                                    "started_at": "2026-02-24T10:00:00+00:00",
                                    "finished_at": "2026-02-24T10:00:03+00:00",
                                }
                            },
                        }
                    ],
                    "resource_requests": {},
                    "resource_limits": {},
                },
                "pod_conditions": [{"type": "Ready", "status": "False", "reason": "ContainersNotReady"}],
                "pod_events": [
                    {
                        "type": "Warning",
                        "reason": "BackOff",
                        "message": "Back-off restarting failed container",
                        "count": 15,
                    },
                ],
                "owner_chain": {"workload": {"kind": "Deployment", "name": "my-app"}},
                "rollout_status": None,
                "errors": [],
            },
        ),
        "prom_not_healthy": patch("agent.collectors.pod_baseline.query_pod_not_healthy", return_value=None),
        "prom_restarts": patch("agent.collectors.pod_baseline.query_pod_restarts", return_value=None),
        "prom_cpu": patch("agent.collectors.pod_baseline.query_cpu_usage_and_limits", return_value=None),
        "prom_mem": patch("agent.collectors.pod_baseline.query_memory_usage_and_limits", return_value=None),
        "logs": patch(
            "agent.collectors.pod_baseline.fetch_recent_logs",
            return_value={
                "entries": [{"message": "Connection refused to redis:6379"}],
                "status": "ok",
                "reason": None,
                "backend": "victorialogs",
            },
        ),
        "k8s_provider": patch("agent.collectors.crashloop.get_k8s_provider"),
    }


class TestCrashloopCollector:
    """Test crashloop evidence collector."""

    def test_sets_playbook_to_crashloop(self):
        """Test that the collector sets playbook to 'crashloop'."""
        inv = _make_investigation()
        mocks = _mock_providers()
        with mocks["k8s_context"], mocks["prom_not_healthy"], mocks["prom_restarts"], mocks["prom_cpu"], mocks[
            "prom_mem"
        ], mocks["logs"], mocks["k8s_provider"]:
            collect_crashloop_evidence(inv)
        assert inv.target.playbook == "crashloop"

    def test_missing_pod_target_appends_error(self):
        """Test that missing pod target appends error."""
        inv = _make_investigation(target=TargetRef(namespace=None, pod=None))
        collect_crashloop_evidence(inv)
        assert any("missing pod/namespace" in e for e in inv.errors)

    def test_collects_k8s_context(self):
        """Test that K8s context is gathered."""
        inv = _make_investigation()
        mocks = _mock_providers()
        with mocks["k8s_context"] as mock_ctx, mocks["prom_not_healthy"], mocks["prom_restarts"], mocks[
            "prom_cpu"
        ], mocks["prom_mem"], mocks["logs"], mocks["k8s_provider"]:
            collect_crashloop_evidence(inv)
        mock_ctx.assert_called_once()
        assert inv.evidence.k8s.pod_info is not None

    def test_fetches_previous_container_logs(self):
        """Test that previous container logs are fetched via K8s API."""
        inv = _make_investigation()
        mocks = _mock_providers()
        mock_k8s = MagicMock()
        mock_k8s.read_pod_log.return_value = "ERROR: Connection refused\nFATAL: Cannot start"

        with mocks["k8s_context"], mocks["prom_not_healthy"], mocks["prom_restarts"], mocks["prom_cpu"], mocks[
            "prom_mem"
        ], mocks["logs"], patch("agent.collectors.crashloop.get_k8s_provider", return_value=mock_k8s):
            collect_crashloop_evidence(inv)

        mock_k8s.read_pod_log.assert_called_once()
        assert inv.meta.get("previous_container_logs") == "ERROR: Connection refused\nFATAL: Cannot start"

    def test_parses_previous_logs(self):
        """Test that previous logs are parsed for error patterns."""
        inv = _make_investigation()
        mocks = _mock_providers()
        mock_k8s = MagicMock()
        mock_k8s.read_pod_log.return_value = "ERROR: Connection refused to redis:6379\nINFO: Starting up"

        with mocks["k8s_context"], mocks["prom_not_healthy"], mocks["prom_restarts"], mocks["prom_cpu"], mocks[
            "prom_mem"
        ], mocks["logs"], patch("agent.collectors.crashloop.get_k8s_provider", return_value=mock_k8s):
            collect_crashloop_evidence(inv)

        # Should have parsed errors from previous logs
        prev_errors = inv.meta.get("previous_logs_parsed_errors")
        assert prev_errors is not None or inv.meta.get("previous_container_logs") is not None

    def test_detects_liveness_probe_failure(self):
        """Test probe failure detection from events."""
        inv = _make_investigation()
        mocks = _mock_providers()

        # Override k8s_context to include liveness probe events
        liveness_ctx = patch(
            "agent.collectors.pod_baseline.gather_pod_context",
            return_value={
                "pod_info": {
                    "name": "my-app-abc123",
                    "namespace": "default",
                    "containers": [],
                    "container_statuses": [],
                },
                "pod_conditions": [],
                "pod_events": [
                    {
                        "type": "Warning",
                        "reason": "Unhealthy",
                        "message": "Liveness probe failed: HTTP probe failed with statuscode: 503",
                        "count": 5,
                    },
                    {
                        "type": "Warning",
                        "reason": "BackOff",
                        "message": "Back-off restarting failed container",
                        "count": 10,
                    },
                ],
                "owner_chain": None,
                "rollout_status": None,
                "errors": [],
            },
        )

        with liveness_ctx, mocks["prom_not_healthy"], mocks["prom_restarts"], mocks["prom_cpu"], mocks[
            "prom_mem"
        ], mocks["logs"], mocks["k8s_provider"]:
            collect_crashloop_evidence(inv)

        assert inv.meta.get("probe_failure_type") == "liveness"

    def test_detects_readiness_probe_failure(self):
        """Test readiness probe detection from events."""
        inv = _make_investigation()
        mocks = _mock_providers()

        readiness_ctx = patch(
            "agent.collectors.pod_baseline.gather_pod_context",
            return_value={
                "pod_info": {
                    "name": "my-app-abc123",
                    "namespace": "default",
                    "containers": [],
                    "container_statuses": [],
                },
                "pod_conditions": [],
                "pod_events": [
                    {
                        "type": "Warning",
                        "reason": "Unhealthy",
                        "message": "Readiness probe failed: dial tcp 10.0.0.1:8080: connect: connection refused",
                        "count": 3,
                    },
                ],
                "owner_chain": None,
                "rollout_status": None,
                "errors": [],
            },
        )

        with readiness_ctx, mocks["prom_not_healthy"], mocks["prom_restarts"], mocks["prom_cpu"], mocks[
            "prom_mem"
        ], mocks["logs"], mocks["k8s_provider"]:
            collect_crashloop_evidence(inv)

        assert inv.meta.get("probe_failure_type") == "readiness"

    def test_extracts_crash_timing(self):
        """Test crash duration extraction from container statuses."""
        inv = _make_investigation()
        mocks = _mock_providers()

        with mocks["k8s_context"], mocks["prom_not_healthy"], mocks["prom_restarts"], mocks["prom_cpu"], mocks[
            "prom_mem"
        ], mocks["logs"], mocks["k8s_provider"]:
            collect_crashloop_evidence(inv)

        # The mock pod_info has started_at and finished_at 3s apart
        assert inv.meta.get("crash_duration_seconds") == 3

    def test_oom_exit_code_137(self):
        """Test with OOMKilled exit code 137."""
        inv = _make_investigation()
        mocks = _mock_providers()

        oom_ctx = patch(
            "agent.collectors.pod_baseline.gather_pod_context",
            return_value={
                "pod_info": {
                    "name": "my-app-abc123",
                    "namespace": "default",
                    "containers": [{"name": "my-app", "image": "my-app:v1.0"}],
                    "container_statuses": [
                        {
                            "name": "my-app",
                            "ready": False,
                            "restart_count": 3,
                            "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                            "last_state": {
                                "terminated": {
                                    "exit_code": 137,
                                    "reason": "OOMKilled",
                                    "started_at": "2026-02-24T10:00:00+00:00",
                                    "finished_at": "2026-02-24T10:01:30+00:00",
                                }
                            },
                        }
                    ],
                    "resource_requests": {},
                    "resource_limits": {},
                },
                "pod_conditions": [],
                "pod_events": [],
                "owner_chain": None,
                "rollout_status": None,
                "errors": [],
            },
        )

        with oom_ctx, mocks["prom_not_healthy"], mocks["prom_restarts"], mocks["prom_cpu"], mocks["prom_mem"], mocks[
            "logs"
        ], mocks["k8s_provider"]:
            collect_crashloop_evidence(inv)

        assert inv.meta.get("crash_duration_seconds") == 90


class TestCrashloopPlaybookRegistration:
    """Test that crashloop playbook is properly registered."""

    def test_playbook_registered_for_kubepodcrashlooping(self):
        from agent.playbooks import get_playbook_for_alert

        pb = get_playbook_for_alert("KubePodCrashLooping")
        assert pb is not None
        assert pb.__name__ == "investigate_crashloop_playbook"

    def test_playbook_registered_for_strict_variant(self):
        from agent.playbooks import get_playbook_for_alert

        pb = get_playbook_for_alert("KubePodCrashLoopingStrict")
        assert pb is not None
        assert pb.__name__ == "investigate_crashloop_playbook"
