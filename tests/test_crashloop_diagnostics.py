"""Unit tests for crashloop diagnostic module and patterns."""

from datetime import datetime, timezone

from agent.core.models import (
    DerivedFeatures,
    Evidence,
    FeaturesK8s,
    Investigation,
    K8sContainerLastTerminated,
    LogsEvidence,
    TargetRef,
    TimeWindow,
)
from agent.diagnostics.crashloop_diagnostics import CrashLoopDiagnosticModule
from agent.diagnostics.patterns.crashloop_patterns import (
    CRASHLOOP_CONFIG_MISSING,
    CRASHLOOP_DATABASE_CONNECTION,
    CRASHLOOP_DEPENDENCY_CONNECTION,
    CRASHLOOP_OOM_APPLICATION,
    CRASHLOOP_PERMISSION_DENIED,
    CRASHLOOP_PORT_BIND_FAILURE,
)


def _make_investigation(**kwargs) -> Investigation:
    now = datetime.now(timezone.utc)
    defaults = {
        "alert": {"labels": {"alertname": "KubePodCrashLooping"}},
        "time_window": TimeWindow(window="1h", start_time=now, end_time=now),
        "target": TargetRef(namespace="test-ns", pod="test-pod"),
        "evidence": Evidence(),
    }
    defaults.update(kwargs)
    return Investigation(**defaults)


class TestCrashloopPatterns:
    """Test each crashloop log pattern matches expected log lines."""

    def test_dependency_connection_patterns(self):
        test_cases = [
            "Connection refused to redis:6379",
            "ECONNREFUSED 127.0.0.1:5432",
            "dial tcp 10.96.0.1:443: connection refused",
            "no such host api-gateway.default.svc.cluster.local",
            "Name or service not known for postgres-master",
            "getaddrinfo ENOTFOUND cache-service",
        ]
        for log_text in test_cases:
            assert CRASHLOOP_DEPENDENCY_CONNECTION.matches(log_text), f"Should match: {log_text}"

    def test_dependency_connection_no_false_positives(self):
        non_matches = [
            "Successfully connected to database",
            "Server started on port 8080",
            "Processing request completed",
        ]
        for log_text in non_matches:
            assert not CRASHLOOP_DEPENDENCY_CONNECTION.matches(log_text), f"Should NOT match: {log_text}"

    def test_dependency_connection_host_extraction(self):
        ctx = CRASHLOOP_DEPENDENCY_CONNECTION.extract_context("dial tcp redis-master:6379: connection refused")
        assert ctx.get("host") == "redis-master"

    def test_config_missing_patterns(self):
        test_cases = [
            "FileNotFoundError: [Errno 2] No such file or directory: '/etc/config/app.yaml'",
            "No such file or directory /config/settings.json",
            "missing required config key DATABASE_URL",
            "ENOENT: no such file or directory, open '/app/config.yaml'",
            "KeyError: 'DATABASE_URL'",
        ]
        for log_text in test_cases:
            assert CRASHLOOP_CONFIG_MISSING.matches(log_text), f"Should match: {log_text}"

    def test_port_bind_patterns(self):
        test_cases = [
            "bind: address already in use",
            "Error: listen EADDRINUSE: address already in use :::3000",
            "listen tcp :8080: bind: address already in use",
        ]
        for log_text in test_cases:
            assert CRASHLOOP_PORT_BIND_FAILURE.matches(log_text), f"Should match: {log_text}"

    def test_oom_application_patterns(self):
        test_cases = [
            "java.lang.OutOfMemoryError: Java heap space",
            "FATAL ERROR: CALL_AND_RETRY_LAST Allocation failed - JavaScript heap out of memory",
            "Cannot allocate memory (errno=12)",
            "ENOMEM",
            "runtime: out of memory: cannot allocate",
            "MemoryError: Unable to allocate array",
        ]
        for log_text in test_cases:
            assert CRASHLOOP_OOM_APPLICATION.matches(log_text), f"Should match: {log_text}"

    def test_permission_denied_patterns(self):
        test_cases = [
            "Permission denied: '/data/logs/app.log'",
            "EACCES: permission denied, open '/tmp/data'",
            "Operation not permitted",
            "read-only file system",
        ]
        for log_text in test_cases:
            assert CRASHLOOP_PERMISSION_DENIED.matches(log_text), f"Should match: {log_text}"

    def test_database_connection_patterns(self):
        test_cases = [
            'could not connect to server: Connection refused. Is the server running on host "localhost" and accepting TCP/IP connections on port 5432? (PostgreSQL)',
            "Access denied for user 'root'@'10.0.0.1' (using password: YES) MySQL",
            "Cannot connect to Redis at redis-master:6379",
            "MongoNetworkError: connect ECONNREFUSED 10.0.0.5:27017",
            'FATAL:  password authentication failed for user "app"',
        ]
        for log_text in test_cases:
            assert CRASHLOOP_DATABASE_CONNECTION.matches(log_text), f"Should match: {log_text}"

    def test_database_type_extraction(self):
        ctx = CRASHLOOP_DATABASE_CONNECTION.extract_context("could not connect to server PostgreSQL on port 5432")
        assert ctx.get("db_type") == "PostgreSQL"


class TestCrashLoopDiagnosticModule:
    """Test the CrashLoopDiagnosticModule."""

    def test_applies_to_crashloop_family(self):
        module = CrashLoopDiagnosticModule()
        inv = _make_investigation(meta={"family": "crashloop"})
        assert module.applies(inv)

    def test_does_not_apply_to_other_families(self):
        module = CrashLoopDiagnosticModule()
        inv = _make_investigation(meta={"family": "job_failed"})
        assert not module.applies(inv)

    def test_does_not_apply_to_generic(self):
        module = CrashLoopDiagnosticModule()
        inv = _make_investigation(meta={"family": "generic"})
        assert not module.applies(inv)

    def test_diagnose_exit_code_137_oom(self):
        """Test OOM hypothesis from exit code 137."""
        module = CrashLoopDiagnosticModule()
        inv = _make_investigation(
            meta={"family": "crashloop"},
        )
        inv.analysis.features = DerivedFeatures(
            family="crashloop",
            k8s=FeaturesK8s(
                container_last_terminated_top=[
                    K8sContainerLastTerminated(container="my-app", reason="OOMKilled", exit_code=137)
                ]
            ),
        )

        hypotheses = module.diagnose(inv)

        oom = next((h for h in hypotheses if h.hypothesis_id == "crashloop_oom"), None)
        assert oom is not None
        assert oom.confidence_0_100 == 80
        assert "137" in oom.title or "OOM" in oom.title

    def test_diagnose_exit_code_139_segfault(self):
        """Test segfault hypothesis from exit code 139."""
        module = CrashLoopDiagnosticModule()
        inv = _make_investigation(meta={"family": "crashloop"})
        inv.analysis.features = DerivedFeatures(
            family="crashloop",
            k8s=FeaturesK8s(
                container_last_terminated_top=[
                    K8sContainerLastTerminated(container="my-app", reason="Error", exit_code=139)
                ]
            ),
        )

        hypotheses = module.diagnose(inv)

        segfault = next((h for h in hypotheses if h.hypothesis_id == "crashloop_segfault"), None)
        assert segfault is not None
        assert segfault.confidence_0_100 == 75

    def test_diagnose_exit_code_0_liveness(self):
        """Test liveness probe hypothesis from exit code 0."""
        module = CrashLoopDiagnosticModule()
        inv = _make_investigation(meta={"family": "crashloop"})
        inv.analysis.features = DerivedFeatures(
            family="crashloop",
            k8s=FeaturesK8s(
                container_last_terminated_top=[
                    K8sContainerLastTerminated(container="my-app", reason="Completed", exit_code=0)
                ]
            ),
        )

        hypotheses = module.diagnose(inv)

        liveness = next((h for h in hypotheses if h.hypothesis_id == "crashloop_liveness_probe"), None)
        assert liveness is not None
        assert liveness.confidence_0_100 == 70

    def test_diagnose_exit_code_1_fast_crash(self):
        """Test app error hypothesis with fast crash timing."""
        module = CrashLoopDiagnosticModule()
        inv = _make_investigation(meta={"family": "crashloop", "crash_duration_seconds": 3})
        inv.analysis.features = DerivedFeatures(
            family="crashloop",
            k8s=FeaturesK8s(
                container_last_terminated_top=[
                    K8sContainerLastTerminated(container="my-app", reason="Error", exit_code=1)
                ]
            ),
        )

        hypotheses = module.diagnose(inv)

        app_err = next((h for h in hypotheses if h.hypothesis_id == "crashloop_app_error"), None)
        assert app_err is not None
        assert app_err.confidence_0_100 == 65  # Higher confidence for instant crash

    def test_diagnose_log_pattern_matching(self):
        """Test that log patterns generate hypotheses."""
        module = CrashLoopDiagnosticModule()
        inv = _make_investigation(
            meta={"family": "crashloop"},
            evidence=Evidence(
                logs=LogsEvidence(
                    parsed_errors=[
                        {"message": "ECONNREFUSED 10.96.0.1:6379 - Cannot connect to Redis", "severity": "ERROR"},
                    ]
                ),
            ),
        )
        inv.analysis.features = DerivedFeatures(
            family="crashloop",
            k8s=FeaturesK8s(),
        )

        hypotheses = module.diagnose(inv)

        # Should have a dependency connection hypothesis from log matching
        dep = next((h for h in hypotheses if h.hypothesis_id == "crashloop_dependency_connection"), None)
        assert dep is not None
        assert dep.confidence_0_100 == 85

    def test_diagnose_previous_logs_matched(self):
        """Test that previous container logs are also pattern-matched."""
        module = CrashLoopDiagnosticModule()
        inv = _make_investigation(
            meta={
                "family": "crashloop",
                "previous_logs_parsed_errors": [
                    {"message": "FileNotFoundError: /etc/config/app.yaml", "severity": "ERROR"}
                ],
            },
        )
        inv.analysis.features = DerivedFeatures(
            family="crashloop",
            k8s=FeaturesK8s(),
        )

        hypotheses = module.diagnose(inv)

        config = next((h for h in hypotheses if h.hypothesis_id == "crashloop_config_missing"), None)
        assert config is not None

    def test_diagnose_fallback_when_no_patterns(self):
        """Test generic fallback when no patterns match."""
        module = CrashLoopDiagnosticModule()
        inv = _make_investigation(meta={"family": "crashloop"})
        inv.analysis.features = DerivedFeatures(
            family="crashloop",
            k8s=FeaturesK8s(),
        )

        hypotheses = module.diagnose(inv)

        assert len(hypotheses) >= 1
        generic = next((h for h in hypotheses if h.hypothesis_id == "crashloop_generic"), None)
        assert generic is not None
        assert generic.confidence_0_100 >= 55

    def test_probe_failure_event_hypothesis(self):
        """Test liveness probe failure from events generates hypothesis."""
        module = CrashLoopDiagnosticModule()
        inv = _make_investigation(
            meta={"family": "crashloop", "probe_failure_type": "liveness"},
        )
        inv.analysis.features = DerivedFeatures(
            family="crashloop",
            k8s=FeaturesK8s(
                container_last_terminated_top=[
                    K8sContainerLastTerminated(container="my-app", reason="Error", exit_code=1)
                ]
            ),
        )

        hypotheses = module.diagnose(inv)

        liveness = next((h for h in hypotheses if h.hypothesis_id == "crashloop_liveness_probe_failure"), None)
        assert liveness is not None
        assert liveness.confidence_0_100 == 75
