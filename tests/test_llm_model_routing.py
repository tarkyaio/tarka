"""Tests for multi-model LLM routing (_resolve_model / call_site)."""

from __future__ import annotations


def test_resolve_default_no_light_model(monkeypatch) -> None:
    """When LLM_MODEL_LIGHT is unset, all sites use LLM_MODEL."""
    monkeypatch.setenv("LLM_MODEL", "my-heavy-model")
    monkeypatch.delenv("LLM_MODEL_LIGHT", raising=False)

    from agent.llm.client import _resolve_model

    for site in ("enrichment", "rca_planner", "global_chat", "rca_synthesis", "case_chat", "default", "streaming"):
        assert _resolve_model(site) == "my-heavy-model", f"site={site} should use LLM_MODEL"


def test_resolve_light_site_uses_light_model(monkeypatch) -> None:
    """Light call sites use LLM_MODEL_LIGHT when it is set."""
    monkeypatch.setenv("LLM_MODEL", "heavy")
    monkeypatch.setenv("LLM_MODEL_LIGHT", "light")

    from agent.llm.client import _LIGHT_SITES, _resolve_model

    for site in _LIGHT_SITES:
        assert _resolve_model(site) == "light", f"site={site} should use LLM_MODEL_LIGHT"


def test_resolve_heavy_site_ignores_light_model(monkeypatch) -> None:
    """Heavy call sites always use LLM_MODEL even when LLM_MODEL_LIGHT is set."""
    monkeypatch.setenv("LLM_MODEL", "heavy")
    monkeypatch.setenv("LLM_MODEL_LIGHT", "light")

    from agent.llm.client import _resolve_model

    for site in ("rca_synthesis", "case_chat", "default", "streaming"):
        assert _resolve_model(site) == "heavy", f"site={site} should use LLM_MODEL"


def test_resolve_defaults_when_no_env(monkeypatch) -> None:
    """Falls back to gemini-2.5-flash when neither LLM_MODEL nor LLM_MODEL_LIGHT is set."""
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_MODEL_LIGHT", raising=False)

    from agent.llm.client import _resolve_model

    assert _resolve_model("enrichment") == "gemini-2.5-flash"
    assert _resolve_model("default") == "gemini-2.5-flash"


def test_call_site_passed_through_usage(monkeypatch) -> None:
    """Verify usage dict includes model and call_site when returned."""
    monkeypatch.setenv("LLM_MODEL", "heavy")
    monkeypatch.setenv("LLM_MODEL_LIGHT", "light")
    monkeypatch.delenv("LLM_MOCK", raising=False)

    import agent.llm.client as llm_client

    # Stub out LLM instance to return a fake message with usage
    class FakeMsg:
        content = '{"summary": "ok"}'
        usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}

    class FakeLLM:
        def invoke(self, prompt):
            return FakeMsg()

    monkeypatch.setattr(llm_client, "_get_llm_instance", lambda p, cfg, enable_thinking=True: (FakeLLM(), None))

    # Test light site
    obj, err, usage = llm_client.generate_json("test prompt", call_site="enrichment")
    assert err is None
    assert usage is not None
    assert usage["model"] == "light"
    assert usage["call_site"] == "enrichment"
    assert usage["input_tokens"] == 10

    # Test heavy site
    obj, err, usage = llm_client.generate_json("test prompt", call_site="case_chat")
    assert err is None
    assert usage is not None
    assert usage["model"] == "heavy"
    assert usage["call_site"] == "case_chat"


def test_load_config_model_override(monkeypatch) -> None:
    """_load_config with model_override uses the override, not the env var."""
    monkeypatch.setenv("LLM_MODEL", "env-model")

    from agent.llm.client import _load_config

    cfg = _load_config(model_override="override-model")
    assert cfg.model == "override-model"

    cfg_default = _load_config()
    assert cfg_default.model == "env-model"
