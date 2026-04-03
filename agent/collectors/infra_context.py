"""Collect service-scoped evidence from org-wide infrastructure repos.

Reads config/infra-context-repos.yaml, mirrors each listed repo, finds paths
relevant to the investigation's service, diffs them against a pre-incident
baseline, and extracts typed change signals (Argo CD / Terraform).

Gated by GITHUB_EVIDENCE_ENABLED=1. Returns an empty list when the config
file is absent, has an empty repos list, or when the env flag is off.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from agent.core.models import (
    InfraChangeSignal,
    InfraFileContext,
    InfraRepoEvidence,
    Investigation,
)
from agent.providers.git_mirror_provider import get_git_mirror_cache

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Preset repo type definitions
# ---------------------------------------------------------------------------

_PRESETS: Dict[str, Dict[str, Any]] = {
    "argocd": {
        "display_name": "Argo CD",
        "search_roots": ["apps/", ""],
        "extensions": [".yaml", ".yml"],
    },
    "flux": {
        "display_name": "Flux CD",
        "search_roots": ["apps/", "clusters/", ""],
        "extensions": [".yaml", ".yml"],
    },
    "terraform": {
        "display_name": "Terraform",
        "search_roots": ["services/", "environments/", ""],
        "extensions": [".tf", ".tfvars", ".hcl"],
    },
    "terraform_modules": {
        "display_name": "Terraform Modules",
        "search_roots": ["modules/", ""],
        "extensions": [".tf", ".hcl"],
    },
    "generic": {
        "display_name": "Infrastructure",
        "search_roots": [""],
        "extensions": [".yaml", ".yml", ".tf", ".json"],
    },
}

# ---------------------------------------------------------------------------
# Terraform resource-type → failure category mapping
# ---------------------------------------------------------------------------

_TF_CATEGORY_MAP: List[tuple[str, str]] = [
    ("aws_security_group", "networking"),
    ("aws_vpc", "networking"),
    ("aws_subnet", "networking"),
    ("aws_route", "networking"),
    ("aws_db_instance", "database"),
    ("aws_rds_", "database"),
    ("aws_iam_", "permissions"),
    ("aws_lb_", "load_balancer"),
    ("aws_alb_", "load_balancer"),
    ("aws_autoscaling_", "scaling"),
    ("aws_appautoscaling_", "scaling"),
    ("aws_s3_", "storage"),
    ("aws_efs_", "storage"),
    ("kubernetes_", "kubernetes"),
    ("google_", "cloud_infra"),
    ("azurerm_", "cloud_infra"),
]

_CONFIG_PATH = Path(__file__).parents[2] / "config" / "infra-context-repos.yaml"

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_infra_repo_configs() -> List[Dict[str, Any]]:
    """Return the list of repo config dicts from infra-context-repos.yaml."""
    if not _CONFIG_PATH.exists():
        return []
    try:
        with open(_CONFIG_PATH) as f:
            data = yaml.safe_load(f) or {}
        return data.get("repos") or []
    except Exception as e:
        logger.warning("infra-context: failed to load config: %s", e)
        return []


def collect_infra_context(investigation: Investigation) -> List[InfraRepoEvidence]:
    """Collect service-scoped evidence from all configured infra repos.

    Returns an empty list (never raises) when disabled or misconfigured.
    """
    if not os.getenv("GITHUB_EVIDENCE_ENABLED", "").strip() in ("1", "true", "True"):
        return []

    configs = load_infra_repo_configs()
    if not configs:
        return []

    service_name = _extract_service_name(investigation)
    if not service_name:
        return []

    time_window = investigation.time_window
    mirror = get_git_mirror_cache()
    results: List[InfraRepoEvidence] = []

    for cfg in configs:
        repo = cfg.get("repo", "").strip()
        if not repo:
            continue
        repo_type = cfg.get("type", "generic")
        preset = _PRESETS.get(repo_type, _PRESETS["generic"])
        display_name = cfg.get("display_name") or preset["display_name"]
        search_roots: List[str] = cfg.get("search_roots") or preset["search_roots"]
        extensions: List[str] = cfg.get("extensions") or preset["extensions"]

        evidence = InfraRepoEvidence(
            repo=repo,
            type=repo_type,
            display_name=display_name,
            service_name_matched=service_name,
        )

        try:
            mirror_path = mirror.ensure_mirror(repo)
        except Exception as e:
            evidence.errors.append(f"mirror: {e}")
            results.append(evidence)
            continue

        # --- Layer 1: path discovery -----------------------------------------
        matched_paths = _discover_paths(mirror, mirror_path, service_name, search_roots, extensions)

        if not matched_paths:
            results.append(evidence)
            continue

        # --- Layer 1: baseline sha + diff ------------------------------------
        base_sha = _find_base_sha(mirror, mirror_path, time_window)

        for path in matched_paths:
            try:
                raw_content = mirror.read_file(mirror_path, "HEAD", path)
            except Exception as e:
                evidence.errors.append(f"read {path}: {e}")
                continue

            content_lines = raw_content.splitlines()
            truncated = len(content_lines) > 100
            content = "\n".join(content_lines[:100])

            diff_text: Optional[str] = None
            if base_sha:
                try:
                    _, raw_diff = mirror.diff_range(mirror_path, base_sha, "HEAD", pathspec=[path])
                    if raw_diff:
                        diff_lines = raw_diff.splitlines()
                        truncated_diff = len(diff_lines) > 50
                        diff_text = "\n".join(diff_lines[:50])
                        if truncated_diff:
                            diff_text += "\n... (truncated)"
                except Exception as e:
                    evidence.errors.append(f"diff {path}: {e}")

            evidence.files.append(
                InfraFileContext(
                    path=path,
                    content=content,
                    size_lines=len(content_lines),
                    truncated=truncated,
                    diff=diff_text,
                )
            )

        # --- Layer 1: recent commits for matched paths -----------------------
        if matched_paths and time_window:
            try:
                from datetime import timedelta

                since = time_window.start_time - timedelta(hours=48)
                until = time_window.end_time
                evidence.recent_commits = mirror.recent_commits_for_paths(
                    mirror_path,
                    matched_paths,
                    since=since,
                    until=until,
                    max_results=10,
                )
            except Exception as e:
                evidence.errors.append(f"recent_commits_for_paths: {e}")

        # --- Layer 2: structured signal extraction ---------------------------
        for file_ctx in evidence.files:
            try:
                signals = _extract_signals(repo_type, file_ctx, evidence.recent_commits)
                evidence.change_signals.extend(signals)
            except Exception as e:
                evidence.errors.append(f"signal extraction {file_ctx.path}: {e}")

        results.append(evidence)

    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_service_name(investigation: Investigation) -> str:
    """Return the best available service name for infra repo lookups.

    Priority order (most canonical first):
    1. K8s workload labels — app.kubernetes.io/name or app — set intentionally
       by service owners and are what Argo CD / Terraform use as the canonical
       name.  Not subject to K8s naming conventions at all.
    2. Alert labels — app, app_name, service — often set by the alert author.
    3. Regex stripping on workload_name / pod — last resort; fragile because
       naming conventions vary widely and cannot be controlled.
    """
    # 1. K8s workload labels (most canonical)
    try:
        owner_chain = investigation.evidence.k8s.owner_chain or {}
        workload_meta = owner_chain.get("workload") or {}
        labels = workload_meta.get("labels") or {}
        for key in ("app.kubernetes.io/name", "app", "app.kubernetes.io/component"):
            val = (labels.get(key) or "").strip()
            if val:
                return val
    except Exception:
        pass

    # 2. Alert labels
    try:
        alert_labels = investigation.alert.labels or {}
        for key in ("app", "app_name", "service"):
            val = (alert_labels.get(key) or "").strip()
            if val:
                return val
    except Exception:
        pass

    # 3. Regex stripping fallback
    raw = (investigation.target.workload_name or "").strip()
    if not raw:
        raw = (investigation.target.pod or "").strip()
    if not raw:
        return ""
    return _extract_base_service_name(raw)


def _discover_paths(
    mirror: Any,
    mirror_path: Any,
    service_name: str,
    search_roots: List[str],
    extensions: List[str],
) -> List[str]:
    """Find up to 5 service-scoped paths across all search roots.

    Tries two strategies in order:
    1. Path-name matching — fast; works when the folder/file name equals the
       service name (the common case for well-named repos).
    2. Content grep — fallback; finds files by what they *contain* (e.g.
       ``appName: "hourly-five-min-asset-agg"``) regardless of how they are
       named.  Robust to arbitrary naming conventions.
    """
    paths = _discover_paths_by_name(mirror, mirror_path, service_name, search_roots, extensions)
    if not paths:
        paths = _discover_paths_by_grep(mirror, mirror_path, service_name, search_roots, extensions)
    return paths


def _discover_paths_by_name(
    mirror: Any,
    mirror_path: Any,
    service_name: str,
    search_roots: List[str],
    extensions: List[str],
) -> List[str]:
    """Path-name matching strategy (original approach)."""
    exact: List[str] = []
    substring: List[str] = []
    name_lower = service_name.lower()

    for root in search_roots:
        try:
            all_paths = mirror.list_dir_recursive(mirror_path, "HEAD", root, extensions)
        except Exception:
            continue
        for p in all_paths:
            basename = Path(p).stem.lower()
            parent = Path(p).parent.name.lower()
            if basename == name_lower or parent == name_lower:
                exact.append(p)
            elif name_lower in basename or name_lower in parent:
                substring.append(p)

    seen: set[str] = set()
    ranked: List[str] = []
    for p in exact + substring:
        if p not in seen:
            seen.add(p)
            ranked.append(p)
        if len(ranked) >= 5:
            break
    return ranked


def _discover_paths_by_grep(
    mirror: Any,
    mirror_path: Any,
    service_name: str,
    search_roots: List[str],
    extensions: List[str],
) -> List[str]:
    """Content grep fallback strategy."""
    pathspecs = [f":(glob)**{ext}" for ext in extensions] if extensions else None

    try:
        matches = mirror.grep(
            mirror_path,
            "HEAD",
            service_name,
            pathspec=pathspecs,
            ignore_case=True,
            max_results=100,
        )
    except Exception:
        return []

    # Filter to configured search roots when none of them is the catch-all "".
    effective_roots = [r.rstrip("/") for r in search_roots if r.strip()]
    if len(effective_roots) < len(search_roots):
        effective_roots = []

    seen: set[str] = set()
    paths: List[str] = []
    for m in matches:
        p = m["path"]
        if p in seen:
            continue
        if effective_roots and not any(p.startswith(r + "/") or p == r for r in effective_roots):
            continue
        seen.add(p)
        paths.append(p)
        if len(paths) >= 5:
            break
    return paths


def _find_base_sha(mirror: Any, mirror_path: Any, time_window: Any) -> Optional[str]:
    """Return the last commit sha before the incident window minus 2 hours."""
    if not time_window:
        return None
    try:
        from datetime import timedelta, timezone

        window_start = time_window.start_time
        if window_start.tzinfo is None:
            window_start = window_start.replace(tzinfo=timezone.utc)
        baseline = window_start - timedelta(hours=2)
        # Look back 30 days for a base commit
        since = baseline - timedelta(days=30)
        commits = mirror.recent_commits(mirror_path, since=since, until=baseline, branch="HEAD")
        if commits:
            return commits[0]["sha"]
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Layer 2: signal extraction
# ---------------------------------------------------------------------------


def _extract_signals(
    repo_type: str,
    file_ctx: InfraFileContext,
    recent_commits: List[Dict[str, Any]],
) -> List[InfraChangeSignal]:
    if not file_ctx.diff:
        return []
    if repo_type in ("argocd", "flux"):
        return _extract_argocd_signals(file_ctx, recent_commits)
    if repo_type in ("terraform", "terraform_modules"):
        return _extract_terraform_signals(file_ctx, recent_commits)
    return []


def _extract_argocd_signals(
    file_ctx: InfraFileContext,
    recent_commits: List[Dict[str, Any]],
) -> List[InfraChangeSignal]:
    signals: List[InfraChangeSignal] = []
    commit_ts = recent_commits[0]["timestamp"] if recent_commits else None

    try:
        current = yaml.safe_load(file_ctx.content) or {}
    except Exception:
        return signals

    source = (current.get("spec") or {}).get("source") or {}

    # targetRevision change
    old_rev, new_rev = _parse_diff_field_change(file_ctx.diff, "targetRevision")
    if old_rev is not None or new_rev is not None:
        signals.append(
            InfraChangeSignal(
                signal_type="argocd_revision_change",
                timestamp=commit_ts,
                field="spec.source.targetRevision",
                old_value=old_rev,
                new_value=new_rev or source.get("targetRevision"),
                repo_url=source.get("repoURL"),
            )
        )

    # Image tag changes in helm values
    old_tag, new_tag = _parse_diff_field_change(file_ctx.diff, "tag")
    if old_tag is not None or new_tag is not None:
        signals.append(
            InfraChangeSignal(
                signal_type="argocd_image_change",
                timestamp=commit_ts,
                field="spec.source.helm.values[image.tag]",
                old_value=old_tag,
                new_value=new_tag,
                repo_url=source.get("repoURL"),
            )
        )

    # kustomize image overrides
    if "kustomize" in file_ctx.diff and ("images" in file_ctx.diff):
        old_img, new_img = _parse_diff_field_change(file_ctx.diff, "newTag")
        if old_img is not None or new_img is not None:
            signals.append(
                InfraChangeSignal(
                    signal_type="argocd_image_change",
                    timestamp=commit_ts,
                    field="spec.source.kustomize.images[newTag]",
                    old_value=old_img,
                    new_value=new_img,
                    repo_url=source.get("repoURL"),
                )
            )

    return signals


def _parse_diff_field_change(diff: str, field: str) -> tuple[Optional[str], Optional[str]]:
    """Extract old (-) and new (+) values for a yaml field from a unified diff."""
    old_val: Optional[str] = None
    new_val: Optional[str] = None
    pattern = re.compile(rf"^([+-])\s*{re.escape(field)}\s*:\s*(.+)$", re.MULTILINE)
    for m in pattern.finditer(diff):
        sign, value = m.group(1), m.group(2).strip().strip('"').strip("'")
        if sign == "-":
            old_val = value
        else:
            new_val = value
    return old_val, new_val


def _extract_terraform_signals(
    file_ctx: InfraFileContext,
    recent_commits: List[Dict[str, Any]],
) -> List[InfraChangeSignal]:
    signals: List[InfraChangeSignal] = []
    commit_ts = recent_commits[0]["timestamp"] if recent_commits else None

    resource_pattern = re.compile(r'^[+-]\s*resource\s+"([^"]+)"\s+"([^"]+)"', re.MULTILINE)
    resource_types: List[str] = []
    resource_names: List[str] = []

    for m in resource_pattern.finditer(file_ctx.diff):
        rtype, rname = m.group(1), m.group(2)
        if rtype not in resource_types:
            resource_types.append(rtype)
        if rname not in resource_names:
            resource_names.append(rname)

    if not resource_types:
        return signals

    categories: List[str] = []
    for rtype in resource_types:
        for prefix, category in _TF_CATEGORY_MAP:
            if rtype.startswith(prefix) and category not in categories:
                categories.append(category)
                break

    signals.append(
        InfraChangeSignal(
            signal_type="terraform_resource_change",
            timestamp=commit_ts,
            resource_types=resource_types,
            resource_names=resource_names,
            categories=categories,
            files_changed=[file_ctx.path],
        )
    )
    return signals


# ---------------------------------------------------------------------------
# Service name extraction (mirrors agent/collectors/github_context.py)
# ---------------------------------------------------------------------------

_VOWELS = set("aeiou")


def _looks_like_k8s_hash(suffix: str) -> bool:
    if not (5 <= len(suffix) <= 10 and suffix.isalnum() and suffix.islower()):
        return False
    # No vowels: K8s safe alphabet (e.g. "pnhks", "dfnly", "bqrzw")
    if not (_VOWELS & set(suffix)):
        return True
    # Mixed letters and digits: ReplicaSet/Deployment hashes (e.g. "abc123")
    if any(c.isdigit() for c in suffix) and any(c.isalpha() for c in suffix):
        return True
    return False


def _extract_base_service_name(service_name: str) -> str:
    """Strip K8s pod/job suffixes to recover the base service name."""
    m = re.match(r"^(.+)-\d+-\d+-([a-z0-9]{5,10})$", service_name)
    if m:
        service_name = m.group(1)
    else:
        parts = service_name.rsplit("-", 1)
        if len(parts) == 2 and _looks_like_k8s_hash(parts[1]):
            service_name = parts[0]
        m2 = re.match(r"^(.+)-\d+-\d+$", service_name)
        if m2:
            service_name = m2.group(1)
    m3 = re.match(r"^(.+)-(\d{8,10})$", service_name)
    if m3:
        service_name = m3.group(1)
    return service_name
