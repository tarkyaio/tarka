from __future__ import annotations

from datetime import datetime, timedelta


def _mk_inv(*, meta_family: str | None, playbook: str | None, alertname: str = "UnknownAlert"):
    from agent.core.family import set_canonical_family
    from agent.core.models import AlertInstance, Investigation, TargetRef, TimeWindow

    end = datetime(2025, 1, 1, 0, 0, 0)
    start = end - timedelta(hours=1)
    inv = Investigation(
        alert=AlertInstance(fingerprint="fp", labels={"alertname": alertname}, annotations={}),
        time_window=TimeWindow(window="1h", start_time=start, end_time=end),
        target=TargetRef(target_type="pod", namespace="default", pod="p"),
    )
    inv.target.playbook = playbook
    if meta_family is not None:
        set_canonical_family(inv, meta_family, source="test")
    return inv


def test_compute_features_uses_canonical_family_when_present():
    from agent.pipeline.features import compute_features

    inv = _mk_inv(meta_family="generic", playbook="cpu_throttling", alertname="TotallyCustomAlert")
    feats = compute_features(inv)
    assert feats.family == "generic"


def test_registry_applicable_uses_meta_family_before_features():
    from agent.diagnostics.registry import get_default_registry

    inv = _mk_inv(meta_family="pod_not_healthy", playbook=None, alertname="KubernetesPodNotHealthy")
    reg = get_default_registry()
    mods = reg.applicable(inv)
    ids = sorted([getattr(m, "module_id", "") for m in mods])
    assert "k8s_lifecycle" in ids
