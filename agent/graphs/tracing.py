from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _trace_exclude_patterns() -> List[str]:
    """
    Comma-separated denylist of run names to skip sending to LangSmith.

    Supports simple prefix wildcards: an entry ending in '*' matches by prefix.
    Examples:
      - "postgres_index_run"
      - "s3_put_*"
      - "tool:k8s_*"
    """
    raw = (os.getenv("LANGSMITH_TRACE_EXCLUDE") or "").strip()
    if not raw:
        return []
    out: List[str] = []
    for x in raw.split(","):
        s = x.strip()
        if s:
            out.append(s)
    return out


def should_trace_run_name(name: str) -> bool:
    """
    Return False if `name` matches LANGSMITH_TRACE_EXCLUDE.
    """
    n = str(name or "").strip()
    if not n:
        return True
    for pat in _trace_exclude_patterns():
        if not pat:
            continue
        if pat.endswith("*"):
            if n.startswith(pat[:-1]):
                return False
        elif n == pat:
            return False
    return True


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "y", "on")


def tracing_enabled() -> bool:
    """
    Return True when LangSmith tracing should be enabled.

    We keep this env-gated so prod can run without traces unless explicitly enabled.
    """
    # LangSmith recommends either flag; we support both.
    want = _env_bool("LANGSMITH_TRACING", False) or _env_bool("LANGCHAIN_TRACING_V2", False)
    if not want:
        return False
    # Avoid crashing runtime if tracing is enabled but no API key is present.
    # We accept both env var names (LangSmith and legacy LangChain).
    key = (os.getenv("LANGSMITH_API_KEY") or "").strip() or (os.getenv("LANGCHAIN_API_KEY") or "").strip()
    if not key:
        logger.warning(
            "LangSmith tracing requested but no API key found (LANGSMITH_API_KEY/LANGCHAIN_API_KEY). Tracing disabled."
        )
        return False
    return True


def _project_name() -> str:
    return (os.getenv("LANGSMITH_PROJECT") or "").strip() or (os.getenv("LANGCHAIN_PROJECT") or "").strip() or "tarka"


def _tags() -> Optional[List[str]]:
    raw = (os.getenv("LANGSMITH_TAGS") or "").strip()
    if not raw:
        return None
    return [x.strip() for x in raw.split(",") if x.strip()]


def _run_name_prefix() -> str:
    return (os.getenv("LANGSMITH_RUN_NAME_PREFIX") or "").strip()


def build_langsmith_callbacks(*, kind: str, metadata: Optional[Dict[str, Any]] = None) -> List[Any]:
    """
    Build callbacks list for LangSmith tracing (LangSmith Cloud).

    Returns [] when tracing is disabled.
    """
    if not tracing_enabled():
        return []

    try:
        # Lazy imports so non-tracing runs have no extra dependencies at import time.
        from langchain_core.tracers.langchain import LangChainTracer  # type: ignore[import-not-found]
        from langsmith import Client  # type: ignore[import-not-found]
    except Exception as e:
        logger.warning("LangSmith tracing enabled but dependencies unavailable: %s", type(e).__name__)
        return []

    # Client pulls LANGSMITH_API_KEY from env by default.
    key = (os.getenv("LANGSMITH_API_KEY") or "").strip() or (os.getenv("LANGCHAIN_API_KEY") or "").strip() or None
    client = Client(api_key=key)
    tags = _tags()
    # We use project_name as the primary grouping; `kind` goes into tags/metadata.
    proj = _project_name()

    md = dict(metadata or {})
    md["kind"] = str(kind or "unknown")

    tracer = LangChainTracer(project_name=proj, client=client, tags=tags)
    # NOTE: metadata is passed via RunnableConfig below; tracer doesn't accept metadata directly.
    return [tracer]


def build_invoke_config(*, kind: str, run_name: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Build a RunnableConfig dict for LangGraph/LangChain invocation.
    """
    if not tracing_enabled():
        return {}

    prefix = _run_name_prefix()
    rn = f"{prefix}{run_name}" if prefix else run_name
    md = dict(metadata or {})
    md["kind"] = str(kind or "unknown")

    callbacks = build_langsmith_callbacks(kind=kind, metadata=md)
    cfg: Dict[str, Any] = {"metadata": md, "run_name": rn}
    if callbacks:
        cfg["callbacks"] = callbacks
    tags = _tags()
    if tags:
        cfg["tags"] = tags
    return cfg


def trace_tool_call(*, tool: str, args: Dict[str, Any], fn) -> Any:
    """
    Create a tool-level span in LangSmith (when tracing enabled) and execute `fn()`.

    `fn` must be a zero-arg callable.
    """
    if not tracing_enabled():
        return fn()
    # Allow skipping noisy tool spans without disabling tracing entirely.
    if not (should_trace_run_name(f"tool:{tool}") and should_trace_run_name(str(tool))):
        return fn()

    try:
        from langsmith.run_helpers import traceable  # type: ignore[import-not-found]
    except Exception:
        # If tracing is enabled but the helper is unavailable, don't break the core workflow.
        return fn()

    # Wrap in a traceable function so LangSmith records timing + I/O.
    @traceable(name=f"tool:{tool}", run_type="tool")
    def _wrapped(_tool: str, _args: Dict[str, Any]):
        return fn()

    try:
        return _wrapped(str(tool), dict(args or {}))
    except Exception:
        # Never break workflows due to tracing transport failures (e.g., egress blocked).
        return fn()
