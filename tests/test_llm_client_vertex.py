from __future__ import annotations

import sys
import types


def test_llm_mock_returns_obj(monkeypatch) -> None:
    monkeypatch.setenv("LLM_MOCK", "1")
    from agent.llm.client import generate_json

    obj, err = generate_json("hello")
    assert err is None
    assert isinstance(obj, dict)
    assert obj.get("summary")


def test_llm_mock_with_schema_returns_envelope(monkeypatch) -> None:
    monkeypatch.setenv("LLM_MOCK", "1")
    from agent.llm.client import generate_json
    from agent.llm.schemas import ToolPlanResponse

    obj, err = generate_json("hello", schema=ToolPlanResponse)
    assert err is None
    assert isinstance(obj, dict)
    assert obj.get("schema_version") == "tarka.tool_plan.v1"
    assert "tool_calls" in obj


def test_vertex_requires_project(monkeypatch) -> None:
    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "vertexai")
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)

    from agent.llm.client import generate_json

    obj, err = generate_json("hello")
    assert obj is None
    assert err == "missing_gcp_project"


def test_vertex_requires_location(monkeypatch) -> None:
    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "vertexai")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)

    from agent.llm.client import generate_json

    obj, err = generate_json("hello")
    assert obj is None
    assert err == "missing_gcp_location"


def test_vertex_success_parses_json(monkeypatch) -> None:
    # Provide required env
    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "vertexai")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.setenv("LLM_MODEL", "gemini-2.5-flash")
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "256")

    # Stub google.auth.default to simulate ADC being present
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
            # Validate we pass the required fields through
            assert kwargs.get("project") == "proj"
            assert kwargs.get("location") == "us-central1"
            assert kwargs.get("model") == "gemini-2.5-flash"

        def invoke(self, _prompt: str):
            return _Msg('{"ok": true, "answer": 42}')

    lc_mod.ChatVertexAI = _ChatVertexAI  # type: ignore[attr-defined]
    sys.modules["langchain_google_vertexai"] = lc_mod

    from agent.llm.client import generate_json

    obj, err = generate_json("hello")
    assert err is None
    assert obj == {"ok": True, "answer": 42}


def test_vertex_schema_structured_output_single_call(monkeypatch) -> None:
    # Provide required env
    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "vertexai")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.setenv("LLM_MODEL", "gemini-2.5-flash")
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "256")

    from agent.llm.schemas import ToolPlanResponse

    # Stub google.auth.default to simulate ADC being present
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

    calls = {"structured_invoke": 0}

    # Stub langchain_google_vertexai.ChatVertexAI
    lc_mod = types.ModuleType("langchain_google_vertexai")

    class _ChatVertexAI:
        def __init__(self, **kwargs):
            assert kwargs.get("project") == "proj"
            assert kwargs.get("location") == "us-central1"
            assert kwargs.get("model") == "gemini-2.5-flash"

        def with_structured_output(self, schema):
            # Ensure we request schema-based structured output
            assert schema is ToolPlanResponse

            class _Structured:
                def invoke(self, _prompt: str):
                    calls["structured_invoke"] += 1
                    return ToolPlanResponse(reply="ok", tool_calls=[])

            return _Structured()

        def invoke(self, _prompt: str):
            raise AssertionError("raw invoke should not be used when schema is provided")

    lc_mod.ChatVertexAI = _ChatVertexAI  # type: ignore[attr-defined]
    sys.modules["langchain_google_vertexai"] = lc_mod

    from agent.llm.client import generate_json

    obj, err = generate_json("hello", schema=ToolPlanResponse)
    assert err is None
    assert obj is not None
    assert obj.get("schema_version") == "tarka.tool_plan.v1"
    assert calls["structured_invoke"] == 1
