from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List, Optional, Set


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "y", "on")


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _split_csv(raw: str) -> List[str]:
    return [x.strip() for x in (raw or "").split(",") if x.strip()]


@dataclass(frozen=True)
class ChatPolicy:
    # Master switch
    enabled: bool = False

    # Tool categories
    allow_promql: bool = True
    allow_k8s_read: bool = True
    allow_k8s_events: bool = True  # K8s events query tool
    allow_logs_query: bool = True
    allow_argocd_read: bool = False  # placeholder provider
    allow_report_rerun: bool = True
    allow_memory_read: bool = True
    allow_aws_read: bool = False  # AWS infrastructure health checks (EC2, EBS, ELB, RDS, etc.)
    allow_github_read: bool = False  # GitHub commits, workflows, docs

    # Scope limits
    namespace_allowlist: Optional[Set[str]] = None
    cluster_allowlist: Optional[Set[str]] = None
    aws_region_allowlist: Optional[Set[str]] = None  # Restrict AWS queries to specific regions
    github_repo_allowlist: Optional[Set[str]] = None  # Restrict GitHub queries to specific repos

    # Cost caps
    max_steps: int = 4
    max_tool_calls: int = 6
    max_log_lines: int = 200
    max_promql_series: int = 200
    max_time_window_seconds: int = 6 * 3600  # 6h

    # Redaction
    redact_secrets: bool = True


@dataclass(frozen=True)
class ActionPolicy:
    """
    Policy for action proposals + approval/execution workflow.

    This is intentionally separate from chat enablement so an admin can:
    - enable actions without enabling chat
    - enable chat without enabling actions
    """

    enabled: bool = False
    # Allowed action types (if None, allow all known types; prefer allowlist in prod).
    action_type_allowlist: Optional[Set[str]] = None

    # Workflow gates
    require_approval: bool = True
    allow_execute: bool = False  # default: no automated execution

    # Scope limits (optional)
    namespace_allowlist: Optional[Set[str]] = None
    cluster_allowlist: Optional[Set[str]] = None

    # Caps
    max_actions_per_case: int = 25


def load_chat_policy() -> ChatPolicy:
    """
    Load chat/tool policy from env (ConfigMap/Secret friendly).

    Recommended vars:
    - CHAT_ENABLED=1
    - CHAT_ALLOW_PROMQL=1
    - CHAT_ALLOW_K8S_READ=1
    - CHAT_ALLOW_K8S_EVENTS=1
    - CHAT_ALLOW_LOGS_QUERY=1
    - CHAT_ALLOW_REPORT_RERUN=1
    - CHAT_ALLOW_MEMORY_READ=1
    - CHAT_ALLOW_AWS_READ=1
    - CHAT_ALLOW_GITHUB_READ=1
    - CHAT_NAMESPACE_ALLOWLIST=prod,staging
    - CHAT_AWS_REGION_ALLOWLIST=us-east-1,us-west-2
    - CHAT_GITHUB_REPO_ALLOWLIST=myorg/repo1,myorg/repo2
    - CHAT_MAX_TOOL_CALLS=6
    - CHAT_MAX_LOG_LINES=200
    - CHAT_MAX_TIME_WINDOW_SECONDS=21600
    """

    ns_allow = _split_csv(os.getenv("CHAT_NAMESPACE_ALLOWLIST", ""))
    cluster_allow = _split_csv(os.getenv("CHAT_CLUSTER_ALLOWLIST", ""))
    aws_region_allow = _split_csv(os.getenv("CHAT_AWS_REGION_ALLOWLIST", ""))
    github_repo_allow = _split_csv(os.getenv("CHAT_GITHUB_REPO_ALLOWLIST", ""))

    return ChatPolicy(
        enabled=_env_bool("CHAT_ENABLED", False),
        allow_promql=_env_bool("CHAT_ALLOW_PROMQL", True),
        allow_k8s_read=_env_bool("CHAT_ALLOW_K8S_READ", True),
        allow_k8s_events=_env_bool("CHAT_ALLOW_K8S_EVENTS", True),
        allow_logs_query=_env_bool("CHAT_ALLOW_LOGS_QUERY", True),
        allow_argocd_read=_env_bool("CHAT_ALLOW_ARGOCD_READ", False),
        allow_report_rerun=_env_bool("CHAT_ALLOW_REPORT_RERUN", True),
        allow_memory_read=_env_bool("CHAT_ALLOW_MEMORY_READ", True),
        allow_aws_read=_env_bool("CHAT_ALLOW_AWS_READ", False),
        allow_github_read=_env_bool("CHAT_ALLOW_GITHUB_READ", False),
        namespace_allowlist=set(ns_allow) if ns_allow else None,
        cluster_allowlist=set(cluster_allow) if cluster_allow else None,
        aws_region_allowlist=set(aws_region_allow) if aws_region_allow else None,
        github_repo_allowlist=set(github_repo_allow) if github_repo_allow else None,
        max_steps=max(1, min(_env_int("CHAT_MAX_STEPS", 4), 8)),
        max_tool_calls=max(1, min(_env_int("CHAT_MAX_TOOL_CALLS", 6), 20)),
        max_log_lines=max(20, min(_env_int("CHAT_MAX_LOG_LINES", 200), 2000)),
        max_promql_series=max(50, min(_env_int("CHAT_MAX_PROMQL_SERIES", 200), 5000)),
        max_time_window_seconds=max(300, min(_env_int("CHAT_MAX_TIME_WINDOW_SECONDS", 6 * 3600), 24 * 3600)),
        redact_secrets=_env_bool("CHAT_REDACT_SECRETS", True),
    )


def load_action_policy() -> ActionPolicy:
    """
    Load action proposal policy from env (ConfigMap/Secret friendly).

    Recommended vars:
    - ACTIONS_ENABLED=0|1
    - ACTIONS_REQUIRE_APPROVAL=1
    - ACTIONS_ALLOW_EXECUTE=0
    - ACTIONS_TYPE_ALLOWLIST=restart_pod,rollout_restart,scale_workload,rollback_workload
    - ACTIONS_NAMESPACE_ALLOWLIST=prod,staging
    - ACTIONS_CLUSTER_ALLOWLIST=cluster-a,cluster-b
    - ACTIONS_MAX_ACTIONS_PER_CASE=25
    """

    ns_allow = _split_csv(os.getenv("ACTIONS_NAMESPACE_ALLOWLIST", ""))
    cluster_allow = _split_csv(os.getenv("ACTIONS_CLUSTER_ALLOWLIST", ""))
    type_allow = _split_csv(os.getenv("ACTIONS_TYPE_ALLOWLIST", ""))

    return ActionPolicy(
        enabled=_env_bool("ACTIONS_ENABLED", False),
        require_approval=_env_bool("ACTIONS_REQUIRE_APPROVAL", True),
        allow_execute=_env_bool("ACTIONS_ALLOW_EXECUTE", False),
        action_type_allowlist=set([t.lower() for t in type_allow]) if type_allow else None,
        namespace_allowlist=set(ns_allow) if ns_allow else None,
        cluster_allowlist=set(cluster_allow) if cluster_allow else None,
        max_actions_per_case=max(1, min(_env_int("ACTIONS_MAX_ACTIONS_PER_CASE", 25), 200)),
    )


_ALWAYS_REDACT_PATTERNS = [
    # API Keys & Tokens (explicit key=value patterns)
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?([a-zA-Z0-9_\-+=/.]{8,})['\"]?"),
    # AWS Credentials
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS Access Key ID
    re.compile(r"\bASIA[0-9A-Z]{16}\b"),  # AWS Session Token
    re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*[a-zA-Z0-9+/]{40}"),
    # Bearer tokens (Authorization headers)
    re.compile(r"(?i)authorization\s*:\s*bearer\s+[a-zA-Z0-9._\-]{20,}"),
    re.compile(r"(?i)\bbearer\s+[a-zA-Z0-9._\-]{20,}"),
    # Database connection strings (redact password only, keep host for diagnostics)
    re.compile(r"(?i)(postgres|mysql|mongodb)://([^:]+):([^@]+)@"),  # Replace with \1://\2:***@
    re.compile(r"(?i)password\s*[:=]\s*['\"]?([^'\";\s]{4,})['\"]?"),
    # Private keys
    re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----[^-]+-----END [A-Z ]+ PRIVATE KEY-----"),
    # JWT tokens (base64.base64.base64)
    re.compile(r"\beyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\b"),
    # Generic high-entropy tokens (but NOT Kubernetes resource names or UUIDs)
    # Match: sk-ant-1234567890abcdef, ghp_1234567890abcdef
    re.compile(r"\b(sk|pk|ghp|gho|ghu|ghs|glpat|xoxb|xoxp|xapp)-[a-zA-Z0-9_\-]{20,}\b"),
]

# Infrastructure patterns (only redacted if LLM_REDACT_INFRASTRUCTURE=true)
_INFRASTRUCTURE_PATTERNS = [
    # Email addresses
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    # Private IP addresses
    re.compile(r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b172\.(1[6-9]|2[0-9]|3[0-1])\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    # AWS Account IDs (12-digit numbers in ARNs)
    re.compile(r"\b\d{12}\b"),
]

_TOKEN_PATTERNS = _ALWAYS_REDACT_PATTERNS  # For backwards compatibility


def redact_text(s: str, *, redact_infrastructure: bool | None = None) -> str:
    """
    Best-effort secret redaction for logs/tool outputs.

    This is not perfect; it is meant to reduce accidental leakage in prompts/UI.

    Args:
        s: Text to redact
        redact_infrastructure: If True, also redact IPs/emails. If None, uses LLM_REDACT_INFRASTRUCTURE env var (default: False)

    Returns:
        Redacted text with secrets replaced by [REDACTED]

    Example:
        >>> redact_text("password=secret123")
        "password=[REDACTED]"
        >>> redact_text("postgres://user:pass@db.example.com/mydb")
        "postgres://user:[REDACTED]@db.example.com/mydb"
    """
    if not s:
        return s

    import os

    out = s

    # Always redact high-risk secrets
    for pat in _ALWAYS_REDACT_PATTERNS:
        out = pat.sub("[REDACTED]", out)

    # Smart replacement for DB URLs: keep host, redact password
    # postgres://user:password@host -> postgres://user:[REDACTED]@host
    out = re.sub(r"(?i)(postgres|mysql|mongodb)://([^:]+):([^@]+)@", r"\1://\2:[REDACTED]@", out)

    # Optionally redact infrastructure details
    if redact_infrastructure is None:
        redact_infrastructure = os.getenv("LLM_REDACT_INFRASTRUCTURE", "").strip().lower() in ("1", "true", "yes")

    if redact_infrastructure:
        for pat in _INFRASTRUCTURE_PATTERNS:
            out = pat.sub("[REDACTED]", out)

    return out
