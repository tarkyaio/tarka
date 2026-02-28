"""Pytest configuration for eval framework."""

import pytest


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "eval: evaluation framework tests (no live cluster dependencies)")


@pytest.fixture(scope="session", autouse=True)
def block_live_provider_calls():
    """
    Safety mechanism: set environment flag for replay mode.

    This indicates to the agent that it's running in eval replay mode.
    The Investigation replay mechanism ensures no live provider calls.
    """
    import os

    # Set environment flag to indicate replay mode
    os.environ["EVAL_REPLAY_MODE"] = "true"

    # Block provider initialization (commented out for now since it may
    # break legitimate uses during fixture loading - we rely on the
    # Investigation replay mechanism instead)
    #
    # def _block_k8s(*args, **kwargs):
    #     raise RuntimeError("Eval test tried to initialize K8s provider!")
    #
    # def _block_prom(*args, **kwargs):
    #     raise RuntimeError("Eval test tried to initialize Prometheus provider!")
    #
    # def _block_logs(*args, **kwargs):
    #     raise RuntimeError("Eval test tried to initialize Logs provider!")
    #
    # monkeypatch.setattr('agent.providers.k8s_provider.get_k8s_provider', _block_k8s)
    # monkeypatch.setattr('agent.providers.prom_provider.get_prom_provider', _block_prom)
    # monkeypatch.setattr('agent.providers.logs_provider.get_logs_provider', _block_logs)


@pytest.fixture(scope="session")
def eval_fixtures_dir():
    """Provide path to eval fixtures directory."""
    from pathlib import Path

    return Path(__file__).parent / "fixtures"


def pytest_collection_modifyitems(config, items):
    """
    Automatically mark all tests in eval/ as eval tests.
    """
    for item in items:
        if "eval" in str(item.fspath):
            item.add_marker(pytest.mark.eval)


def pytest_addoption(parser):
    """Add custom command-line options."""
    parser.addoption(
        "--enable-llm",
        action="store_true",
        default=False,
        help="Enable LLM enrichment in eval tests (may increase runtime and cost)",
    )


@pytest.fixture
def enable_llm(request):
    """Fixture to check if LLM is enabled for testing."""
    return request.config.getoption("--enable-llm")
