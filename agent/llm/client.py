"""
Provider-agnostic LLM client (JSON mode).

Goals:
- Provide a single, uniform way to call any LLM.
- Keep the existing calling contract: `generate_json(prompt) -> (obj, err_code)`.
- Implement robust JSON extraction and stable error classification.
- Never raise (callers already have deterministic fallbacks).

Env (core):
- LLM_PROVIDER: which provider to use (default: "vertexai")
  - vertexai: Gemini via Vertex AI using `langchain_google_vertexai`
  - anthropic: Claude via Anthropic API using `langchain_anthropic`
- LLM_MOCK=1: return a deterministic stub (no external calls)
- LLM_TIMEOUT_SECONDS: HTTP timeout for LLM requests (default: 120, range: 5-180)

Vertex requirements:
- GOOGLE_CLOUD_PROJECT (required)
- GOOGLE_CLOUD_LOCATION (required)
- Application Default Credentials (ADC) must be available (e.g. Workload Identity, or GOOGLE_APPLICATION_CREDENTIALS)

Anthropic requirements:
- ANTHROPIC_API_KEY (required)
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, Type, TypeVar


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "y", "on")


def _provider() -> str:
    return (os.getenv("LLM_PROVIDER") or "").strip().lower() or "vertexai"


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """
    Best-effort extraction for when a model wraps JSON in code fences or adds extra text.
    """
    if not text:
        return None
    t = text.strip()

    # Strip ```json fences if present
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
        t = t.strip()

    # If it looks like a pure JSON object, parse directly.
    if t.startswith("{") and t.endswith("}"):
        try:
            obj = json.loads(t)
            return obj if isinstance(obj, dict) else None
        except Exception:
            pass

    # Fallback: scan for the first balanced JSON object substring and parse it.
    in_str = False
    escape = False
    depth = 0
    start = None

    for i, ch in enumerate(t):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue

        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    candidate = t[start : i + 1]
                    try:
                        obj = json.loads(candidate)
                        return obj if isinstance(obj, dict) else None
                    except Exception:
                        start = None
                        continue
    return None


def _mock_response() -> Dict[str, Any]:
    # Stable stub used by chat/report enrichment when LLM_MOCK=1.
    return {
        "summary": "LLM_MOCK enabled: no external call was made.",
        "likely_root_cause": "mock",
        "confidence": 0.0,
        "evidence": ["mock_mode"],
        "next_steps": ["Disable LLM_MOCK and configure an LLM provider (LLM_PROVIDER)."],
        "unknowns": [],
    }


SchemaT = TypeVar("SchemaT")


@dataclass(frozen=True)
class LLMConfig:
    model: str
    temperature: float
    max_output_tokens: int
    timeout: int = 120


def _load_config() -> LLMConfig:
    model = (os.getenv("LLM_MODEL") or "").strip() or "gemini-2.5-flash"
    try:
        temperature = float((os.getenv("LLM_TEMPERATURE") or "").strip() or "0.2")
    except Exception:
        temperature = 0.2
    try:
        # Prefer a higher default to avoid truncated JSON and retry loops.
        max_output_tokens = int((os.getenv("LLM_MAX_OUTPUT_TOKENS") or "").strip() or "4096")
    except Exception:
        max_output_tokens = 4096
    try:
        # Default 180s timeout for RCA synthesis (which can make multiple tool calls)
        timeout = int((os.getenv("LLM_TIMEOUT_SECONDS") or "").strip() or "180")
    except Exception:
        timeout = 180

    # Keep bounds sane
    temperature = max(0.0, min(temperature, 1.0))
    max_output_tokens = max(64, min(max_output_tokens, 8192))
    timeout = max(5, min(timeout, 300))  # Allow up to 5 minutes for complex multi-step RCA

    return LLMConfig(
        model=model,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        timeout=timeout,
    )


def _vertex_project_location_required() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Return (project, location, err_code). Exactly one of (project/location) may be None only if err_code is set.
    """
    project = (os.getenv("GOOGLE_CLOUD_PROJECT") or "").strip() or None
    location = (os.getenv("GOOGLE_CLOUD_LOCATION") or "").strip() or None
    if not project:
        return None, None, "missing_gcp_project"
    if not location:
        return None, None, "missing_gcp_location"
    return project, location, None


def _classify_error(e: Exception, *, model: str) -> str:
    msg = str(e or "").replace("\n", " ").strip()
    up = msg.upper()

    # Timeout patterns (check BEFORE other patterns for proper SDK retry handling)
    # Check HTTP status codes FIRST (before generic keywords) to avoid false matches
    if isinstance(e, TimeoutError):
        return "timeout"
    if " 408" in msg or "408" in msg:  # HTTP Request Timeout
        return "timeout"
    if " 504" in msg or "504" in msg:  # HTTP Gateway Timeout
        return "gateway_timeout"
    if "DEADLINE_EXCEEDED" in up or "DEADLINE EXCEEDED" in up:
        return "deadline_exceeded"
    if "TIMEOUT" in up or "TIMED OUT" in up:
        return "timeout"

    # Common patterns (work across providers)
    if "PERMISSION_DENIED" in up or " 403" in msg or "403" in msg:
        return "permission_denied"
    if "UNAUTHENTICATED" in up or " 401" in msg or "401" in msg:
        return "unauthenticated"
    if " 404" in msg or "404" in msg or "NOT FOUND" in up:
        return f"model_not_found:{model}"
    if "RATE" in up and "LIMIT" in up:
        return "rate_limited"
    if "MAX_TOKENS" in up or "MAX TOKENS" in up or "CONTEXT LENGTH" in up:
        return "max_tokens_truncated"

    # Anthropic-specific patterns
    if " 429" in msg or "OVERLOADED" in up:
        return "rate_limited"
    if "API_KEY" in up and ("INVALID" in up or "MISSING" in up):
        return "unauthenticated"

    return f"llm_error:{type(e).__name__}"


def _get_llm_instance(provider: str, cfg: LLMConfig, enable_thinking: bool = True) -> Tuple[Any, Optional[str]]:
    """
    Factory function that returns the appropriate LangChain chat model.

    Args:
        provider: LLM provider name
        cfg: LLM configuration
        enable_thinking: Whether to enable extended thinking for Anthropic models

    Returns: (llm_instance, error_code). Exactly one is None.
    """
    if provider in ("vertexai", "vertex", "gcp_vertexai"):
        # Vertex AI / Gemini
        project, location, err = _vertex_project_location_required()
        if err:
            return None, err

        # Preflight ADC so we return stable error codes
        try:
            import google.auth  # type: ignore[import-not-found]
        except Exception:
            return None, "adc_import_failed"
        try:
            google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        except Exception:
            return None, "missing_adc_credentials"

        try:
            from langchain_google_vertexai import ChatVertexAI  # type: ignore[import-not-found]
        except Exception:
            return None, "sdk_import_failed:langchain_google_vertexai"

        llm = ChatVertexAI(
            model=cfg.model,
            temperature=cfg.temperature,
            max_output_tokens=cfg.max_output_tokens,
            project=str(project),
            location=str(location),
            timeout=cfg.timeout,
        )
        return llm, None

    elif provider == "anthropic":
        # Anthropic Claude
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            return None, "missing_api_key"

        try:
            from langchain_anthropic import ChatAnthropic  # type: ignore[import-not-found]
        except Exception:
            return None, "sdk_import_failed:langchain_anthropic"

        # Configure thinking: enabled by default, but disabled for structured output
        # to avoid incompatibility with forced tool calling
        thinking_config = {"type": "enabled", "budget_tokens": 1024} if enable_thinking else None

        llm = ChatAnthropic(
            model=cfg.model,
            temperature=cfg.temperature,
            max_tokens=cfg.max_output_tokens,
            anthropic_api_key=api_key,
            thinking=thinking_config,
            timeout=cfg.timeout,
        )
        return llm, None

    else:
        return None, "provider_not_configured"


def generate_json(
    prompt: str, *, schema: Optional[Type[SchemaT]] = None, enable_thinking: bool = True
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Provider-agnostic JSON call.

    Args:
        prompt: The prompt to send to the LLM
        schema: Optional Pydantic schema for structured output
        enable_thinking: Whether to enable extended thinking (default: True).
                        Automatically disabled for structured output to avoid incompatibility.

    Returns: (obj, err_code). Exactly one is non-None.
    """
    if _env_bool("LLM_MOCK", False):
        if schema is not None:
            # Best-effort: instantiate schema defaults so callers get the right shape.
            try:
                # Pydantic v2 models provide `.model_validate` and `.model_dump`.
                obj0 = getattr(schema, "model_validate")({})  # type: ignore[misc]
                dump = getattr(obj0, "model_dump")(mode="json")  # type: ignore[misc]
                return dump if isinstance(dump, dict) else {}, None
            except Exception:
                return _mock_response(), None
        return _mock_response(), None

    p = _provider()
    cfg = _load_config()

    # Disable thinking for structured output (incompatible with forced tool calling)
    use_thinking = enable_thinking and schema is None

    # Get LangChain model instance using factory
    llm, err = _get_llm_instance(p, cfg, enable_thinking=use_thinking)
    if err:
        return None, err

    try:
        if schema is not None:
            # Use LangChain's with_structured_output (works for both providers)
            structured = llm.with_structured_output(schema)  # type: ignore[call-arg, attr-defined]
            out = structured.invoke(prompt)
            if hasattr(out, "model_dump"):
                d = out.model_dump(mode="json")  # type: ignore[no-any-return]
                return (d, None) if isinstance(d, dict) else (None, "schema_dump_failed")
            if isinstance(out, dict):
                return out, None
            return None, "schema_output_unexpected"

        # Non-schema mode: parse JSON from text
        msg = llm.invoke(prompt)
        text = getattr(msg, "content", None)
        obj = _extract_json_object(str(text or ""))
        return (obj, None) if obj is not None else (None, "json_parse_failed")

    except Exception as e:
        return None, _classify_error(e, model=cfg.model)
