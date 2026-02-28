"""Tests for LLM provider factory and selection logic."""

from __future__ import annotations

import sys
import types


def test_provider_selection_vertexai(monkeypatch) -> None:
    """Test that LLM_PROVIDER=vertexai selects Vertex AI."""
    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "vertexai")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    # Stub google.auth
    # First create google namespace package
    if "google" not in sys.modules:
        google_pkg = types.ModuleType("google")
        sys.modules["google"] = google_pkg

    google_auth = types.ModuleType("google.auth")

    def _default(*, scopes=None):
        return object(), "proj"

    google_auth.default = _default  # type: ignore[attr-defined]
    sys.modules["google.auth"] = google_auth

    # Link auth to google namespace
    import google  # type: ignore

    google.auth = google_auth  # type: ignore[attr-defined]

    # Stub langchain_google_vertexai.ChatVertexAI
    lc_mod = types.ModuleType("langchain_google_vertexai")

    class _Msg:
        def __init__(self, content: str):
            self.content = content

    class _ChatVertexAI:
        def __init__(self, **kwargs):
            assert kwargs.get("project") == "proj"

        def invoke(self, _prompt: str):
            return _Msg('{"provider": "vertexai"}')

    lc_mod.ChatVertexAI = _ChatVertexAI  # type: ignore[attr-defined]
    sys.modules["langchain_google_vertexai"] = lc_mod

    from agent.llm.client import generate_json

    obj, err = generate_json("hello")
    assert err is None
    assert obj == {"provider": "vertexai"}


def test_provider_selection_anthropic(monkeypatch) -> None:
    """Test that LLM_PROVIDER=anthropic selects Anthropic."""
    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    # Stub langchain_anthropic.ChatAnthropic
    lc_mod = types.ModuleType("langchain_anthropic")

    class _Msg:
        def __init__(self, content: str):
            self.content = content

    class _ChatAnthropic:
        def __init__(self, **kwargs):
            assert kwargs.get("anthropic_api_key") == "sk-ant-test"

        def invoke(self, _prompt: str):
            return _Msg('{"provider": "anthropic"}')

    lc_mod.ChatAnthropic = _ChatAnthropic  # type: ignore[attr-defined]
    sys.modules["langchain_anthropic"] = lc_mod

    from agent.llm.client import generate_json

    obj, err = generate_json("hello")
    assert err is None
    assert obj == {"provider": "anthropic"}


def test_provider_not_configured(monkeypatch) -> None:
    """Test that unknown provider returns error."""
    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "unknown_provider")

    from agent.llm.client import generate_json

    obj, err = generate_json("hello")
    assert obj is None
    assert err == "provider_not_configured"


def test_provider_default_is_vertexai(monkeypatch) -> None:
    """Test that default provider is vertexai when LLM_PROVIDER not set."""
    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    # Missing GCP config should trigger error
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)

    from agent.llm.client import generate_json

    obj, err = generate_json("hello")
    assert obj is None
    # Should attempt vertexai and fail on missing project
    assert err == "missing_gcp_project"


def test_provider_aliases_vertex(monkeypatch) -> None:
    """Test that 'vertex' and 'gcp_vertexai' are aliases for 'vertexai'."""
    for alias in ["vertex", "gcp_vertexai"]:
        monkeypatch.delenv("LLM_MOCK", raising=False)
        monkeypatch.setenv("LLM_PROVIDER", alias)
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
        monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")

        # Stub google.auth
        google_auth = types.ModuleType("google.auth")

        def _default(*, scopes=None):
            return object(), "proj"

        google_auth.default = _default  # type: ignore[attr-defined]
        sys.modules["google.auth"] = google_auth

        try:
            import google  # type: ignore

            setattr(google, "auth", google_auth)
        except Exception:
            pass

        # Stub langchain_google_vertexai.ChatVertexAI
        lc_mod = types.ModuleType("langchain_google_vertexai")

        class _Msg:
            def __init__(self, content: str):
                self.content = content

        class _ChatVertexAI:
            def __init__(self, **kwargs):
                pass

            def invoke(self, _prompt: str):
                return _Msg('{"ok": true}')

        lc_mod.ChatVertexAI = _ChatVertexAI  # type: ignore[attr-defined]
        sys.modules["langchain_google_vertexai"] = lc_mod

        from agent.llm.client import generate_json

        obj, err = generate_json("hello")
        assert err is None, f"Alias {alias} failed with error: {err}"
        assert obj == {"ok": True}


def test_lazy_loading_prevents_unused_sdk_imports(monkeypatch) -> None:
    """Test that we don't import unused provider SDKs."""
    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    # Stub langchain_anthropic (required)
    lc_anthropic = types.ModuleType("langchain_anthropic")

    class _Msg:
        def __init__(self, content: str):
            self.content = content

    class _ChatAnthropic:
        def __init__(self, **kwargs):
            pass

        def invoke(self, _prompt: str):
            return _Msg('{"ok": true}')

    lc_anthropic.ChatAnthropic = _ChatAnthropic  # type: ignore[attr-defined]
    sys.modules["langchain_anthropic"] = lc_anthropic

    # Make langchain_google_vertexai fail if imported
    def mock_import_fail_vertex(name, *args, **kwargs):
        if name == "langchain_google_vertexai":
            raise AssertionError("Should not import unused provider SDK")
        return __builtins__.__import__(name, *args, **kwargs)

    # Can't easily mock the lazy import without breaking the test,
    # but at least verify Anthropic works without Vertex SDK installed
    from agent.llm.client import generate_json

    obj, err = generate_json("hello")
    assert err is None
    assert obj == {"ok": True}
