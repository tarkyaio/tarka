from __future__ import annotations


def test_default_registry_contains_expected_modules() -> None:
    from agent.diagnostics.registry import get_default_registry

    reg = get_default_registry()
    ids = [getattr(m, "module_id", "") for m in reg.modules]
    assert ids == [
        "crashloop",  # Crashloop (exit codes + probe failures + log pattern matching)
        "job_failure",  # Job failures (uses log pattern matching framework)
        "k8s_lifecycle",
        "rollout_health",
        "capacity",
        "data_plane",
        "control_plane",
        "observability_pipeline",
    ]
