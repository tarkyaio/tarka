from __future__ import annotations

from datetime import datetime, timezone

from agent.core.models import (
    AlertInstance,
    Analysis,
    Decision,
    DerivedFeatures,
    Evidence,
    FeaturesLogs,
    FeaturesQuality,
    Investigation,
    NoiseInsights,
    TargetRef,
    TimeWindow,
)
from agent.pipeline.verdict import build_base_decision


def _tw() -> TimeWindow:
    now = datetime.now(timezone.utc)
    return TimeWindow(window="15m", start_time=now, end_time=now)


def _investigation(*, alertname: str = "TestAlert") -> Investigation:
    return Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": alertname, "severity": "info"},
            annotations={},
            starts_at=datetime.now(timezone.utc).isoformat(),
            ends_at=None,
            generator_url=None,
            state="active",
            normalized_state="firing",
            ends_at_kind="expires_at",
        ),
        time_window=_tw(),
        target=TargetRef(),
        evidence=Evidence(),
        analysis=Analysis(),
        errors=[],
        meta={},
    )


def _features(*, logs_status: str | None = None, missing_inputs: list[str] | None = None) -> DerivedFeatures:
    q = FeaturesQuality(
        missing_inputs=list(missing_inputs or []),
        # Keep base triage conservative about impact.
        missing_impact_signals=["logs", "http_metrics"],
        impact_signals_available=False,
    )
    logs_feat = FeaturesLogs(
        status=logs_status, backend="victorialogs", reason="request_failed", query_used='{namespace="ns",pod="p"}'
    )
    return DerivedFeatures(family="generic", logs=logs_feat, quality=q)


def test_scenario_a_target_identity_missing() -> None:
    b = _investigation()
    b.target.target_type = "unknown"
    b.analysis.noise = NoiseInsights(
        prometheus={
            "status": "ok",
            "firing_instances": 30,
            "active_instances": 30,
            "selector": '{alertname="TestAlert"}',
        }
    )
    b.analysis.features = _features(logs_status="ok")

    d = build_base_decision(b)
    assert isinstance(d, Decision)
    assert "Broad" in (d.label or "")
    assert "blocked_no_target_identity" in (d.label or "")
    assert any("ALERTS" in x for x in d.next)


def test_scenario_b_k8s_context_missing_for_pod() -> None:
    b = _investigation()
    b.target.target_type = "pod"
    b.target.namespace = "ns"
    b.target.pod = "p"
    b.analysis.noise = NoiseInsights(
        prometheus={"status": "ok", "firing_instances": 3, "active_instances": 3, "selector": '{alertname="TestAlert"}'}
    )
    b.analysis.features = _features(logs_status="ok", missing_inputs=["k8s.pod_info"])

    d = build_base_decision(b)
    assert "blocked_no_k8s_context" in (d.label or "")
    # KSM availability check should be present
    assert any("kube-state-metrics" in x for x in d.next)


def test_scenario_c_logs_missing_unavailable() -> None:
    b = _investigation()
    b.target.target_type = "pod"
    b.target.namespace = "ns"
    b.target.pod = "p"
    b.analysis.noise = NoiseInsights(
        prometheus={"status": "ok", "firing_instances": 2, "active_instances": 2, "selector": '{alertname="TestAlert"}'}
    )
    b.analysis.features = _features(logs_status="unavailable")
    # Indicate logs were attempted
    b.evidence.logs.logs_status = "unavailable"
    b.evidence.logs.logs_backend = "victorialogs"
    b.evidence.logs.logs_reason = "request_failed"

    d = build_base_decision(b)
    assert "logs_missing" in (d.label or "")
    assert any("VictoriaLogs" in x or "Loki" in x for x in d.next)


def test_scenario_d_prometheus_unavailable() -> None:
    b = _investigation()
    b.analysis.noise = NoiseInsights(prometheus={"status": "unavailable", "error": "timeout"})
    b.analysis.features = _features(logs_status="ok")

    d = build_base_decision(b)
    assert "Scope=unknown" in (d.label or "")
    assert "blocked_prometheus_unavailable" in (d.label or "")
    assert any("count(ALERTS" in x for x in d.next)


def test_multi_blocker_d_plus_c() -> None:
    b = _investigation()
    b.target.target_type = "pod"
    b.target.namespace = "ns"
    b.target.pod = "p"
    b.analysis.noise = NoiseInsights(prometheus={"status": "unavailable", "error": "timeout"})
    b.analysis.features = _features(logs_status="empty")
    # Indicate logs were attempted
    b.evidence.logs.logs_status = "empty"
    b.evidence.logs.logs_backend = "victorialogs"

    d = build_base_decision(b)
    assert "Discriminators=" in (d.label or "")
    assert "blocked_prometheus_unavailable" in (d.label or "")
    assert "logs_missing" in (d.label or "")
