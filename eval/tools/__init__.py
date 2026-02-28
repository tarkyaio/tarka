"""Fixture capture and replay tools."""


# Lazy imports to avoid import errors when only using replay
def __getattr__(name):
    if name == "capture_investigation":
        from eval.tools.capture import capture_investigation

        return capture_investigation
    elif name == "create_scenario_template":
        from eval.tools.capture import create_scenario_template

        return create_scenario_template
    elif name == "load_investigation_from_fixture":
        from eval.tools.replay import load_investigation_from_fixture

        return load_investigation_from_fixture
    elif name == "run_investigation_from_fixture":
        from eval.tools.replay import run_investigation_from_fixture

        return run_investigation_from_fixture
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = [
    "capture_investigation",
    "create_scenario_template",
    "load_investigation_from_fixture",
    "run_investigation_from_fixture",
]
