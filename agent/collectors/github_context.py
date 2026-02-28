"""
GitHub evidence collector with multi-step service discovery.

Discovery chain (8 steps, fastest to slowest):
1. K8s annotations
2. Alert labels
3. Service catalog (config/service-catalog.yaml)
4. Third-party catalog (config/third-party-catalog.yaml)
5. Naming convention (GITHUB_DEFAULT_ORG/<workload-name>)
6. Helm metadata
7. OCI image labels
8. Graceful skip
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

# Workload-type suffixes stripped during fuzzy matching in service catalog
# and naming convention discovery.  Shared constant to avoid duplication.
_WORKLOAD_SUFFIXES = (
    "-job",
    "-worker",
    "-cronjob",
    "-migration",
    "-batch",
    "-task",
    "-executor",
    "-handler",
    "-processor",
    "-consumer",
    "-producer",
    "-scheduler",
    "-daemon",
    "-server",
    "-api",
    "-gateway",
)


def discover_github_repo(investigation: Any) -> Optional[str]:
    """
    Discover GitHub repo using multi-step fallback chain.

    Returns:
        "org/repo" string or None if not found
    """
    service_name = investigation.target.workload_name or investigation.target.pod or ""
    namespace = investigation.target.namespace or ""

    # Step 1: K8s annotations
    repo = _discover_from_k8s_annotations(investigation)
    if repo:
        return repo

    # Step 2: Alert labels
    repo = _discover_from_alert_labels(investigation)
    if repo:
        return repo

    # Step 3: Static config (before naming convention — catalog has explicit
    # mappings with fuzzy suffix stripping, so it's more accurate than the
    # convention-based guess which can't verify the repo exists).
    repo = _discover_from_service_catalog(service_name)
    if repo:
        return repo

    # Step 4: Third-party catalog (before naming convention — known
    # third-party services like mysql, redis, etc. should never fall through
    # to the org-based naming convention guess).
    repo = _discover_from_third_party_catalog(service_name)
    if repo:
        return repo

    # Step 5: Naming convention
    repo = _discover_from_naming_convention(service_name)
    if repo:
        return repo

    # Step 6: Helm metadata
    repo = _discover_from_helm_metadata(namespace, service_name)
    if repo:
        return repo

    # Step 7: OCI image labels
    repo = _discover_from_image_labels(investigation)
    if repo:
        return repo

    # Step 8: Graceful skip
    return None


def _discover_from_k8s_annotations(investigation: Any) -> Optional[str]:
    """Step 1: Check K8s workload annotations."""
    try:
        owner_chain = investigation.evidence.k8s.owner_chain
        if not owner_chain:
            return None

        annotations = owner_chain.get("annotations", {})

        # Check standard annotation keys
        repo = annotations.get("github.com/repo") or annotations.get("tarka.io/github-repo")
        if repo and _is_valid_repo_format(repo):
            return repo

    except Exception:
        pass

    return None


def _discover_from_alert_labels(investigation: Any) -> Optional[str]:
    """Step 2: Check Prometheus alert labels."""
    try:
        labels = investigation.alert.labels
        repo = labels.get("github_repo") or labels.get("github_repository")
        if repo and _is_valid_repo_format(repo):
            return repo
    except Exception:
        pass

    return None


def _discover_from_naming_convention(service_name: str) -> Optional[str]:
    """Step 4: Apply naming convention (org/service-name).

    Returns the first candidate that passes the GitHub API HEAD check,
    or falls back to the best-guess candidate if verification is disabled.
    """
    if not service_name:
        return None

    org = os.getenv("GITHUB_DEFAULT_ORG", "")
    if not org:
        return None

    # Clean service name (remove pod suffix if present)
    clean_name = _extract_base_service_name(service_name)

    # Build candidates: exact clean name first, then suffix-stripped variants.
    # E.g. "order-processing-service-executor" →
    #   ["order-processing-service-executor",
    #    "order-processing-service",          (stripped -executor, tried -service)
    #    "order-processing"]                  (stripped -executor)
    candidates = [clean_name]
    for suffix in _WORKLOAD_SUFFIXES:
        if clean_name.endswith(suffix):
            base = clean_name[: -len(suffix)]
            if base:
                candidates.append(base)
                # Also try base-service (common naming pattern)
                candidates.append(f"{base}-service")

    # Dedupe while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique.append(c)

    # Try to verify via GitHub API (fast HEAD check, ~100ms).
    try:
        from agent.providers.github_provider import get_github_provider

        github = get_github_provider()
        for name in unique:
            repo = f"{org}/{name}"
            if github.repo_exists(repo):
                return repo
    except Exception:
        pass  # Verification unavailable; fall back to first candidate

    # Fallback: return best-guess (first candidate) — unverified
    fallback = f"{org}/{unique[0]}"
    logger.warning(
        "GitHub repo discovery fell back to unverified guess %r "
        "(API verification unavailable or all candidates failed)",
        fallback,
    )
    return fallback


def _discover_from_service_catalog(service_name: str) -> Optional[str]:
    """Step 3: Look up in static service catalog."""
    if not service_name:
        return None

    catalog = _load_service_catalog()
    # Build case-insensitive lookup (lowercase keys → config)
    services_raw = catalog.get("services", {})
    services = {k.lower(): v for k, v in services_raw.items()}
    clean_name = _extract_base_service_name(service_name)

    # Try exact match, then base name (case-insensitive)
    for name in [service_name.lower(), clean_name.lower()]:
        service_config = services.get(name)
        if service_config and "github_repo" in service_config:
            repo = service_config["github_repo"]
            if _is_valid_repo_format(repo):
                return repo

    # Fuzzy match: strip workload-type suffixes and try common service name
    # variants (base, base-service).  This handles Job workloads whose catalog
    # entry uses a different suffix, e.g.
    #   workload "order-processing-job"      → catalog "order-processing-service"
    #   workload "order-processing-executor"  → catalog "order-processing-service"
    for name in [service_name.lower(), clean_name.lower()]:
        for suffix in _WORKLOAD_SUFFIXES:
            if name.endswith(suffix):
                base = name[: -len(suffix)]
                # Try base name and base-service variant
                for variant in [base, f"{base}-service"]:
                    service_config = services.get(variant)
                    if service_config and "github_repo" in service_config:
                        repo = service_config["github_repo"]
                        if _is_valid_repo_format(repo):
                            return repo

    return None


def _discover_from_helm_metadata(namespace: str, service_name: str) -> Optional[str]:
    """Step 5: Parse Helm release secrets for chart source."""
    # TODO: Implement Helm secret parsing
    # This requires K8s API access to read secrets
    # For now, return None (optional enhancement)
    return None


def _discover_from_image_labels(investigation: Any) -> Optional[str]:
    """Step 6: Query OCI image labels for source."""
    # TODO: Implement image label querying
    # This requires registry API access
    # For now, return None (optional enhancement)
    return None


def _discover_from_third_party_catalog(service_name: str) -> Optional[str]:
    """Step 7: Look up in third-party catalog."""
    if not service_name:
        return None

    catalog = _load_third_party_catalog()
    clean_name = _extract_base_service_name(service_name)

    # Try exact match, then base name
    for name in [service_name.lower(), clean_name.lower()]:
        service_config = catalog.get("third_party_services", {}).get(name)
        if service_config and "github_repo" in service_config:
            repo = service_config["github_repo"]
            if _is_valid_repo_format(repo):
                return repo

    return None


_VOWELS = set("aeiou")


def _looks_like_k8s_hash(suffix: str) -> bool:
    """Return True if *suffix* looks like a K8s-generated random hash.

    K8s generates 5-char pod suffixes from an alphabet that excludes vowels.
    We match two patterns:
    - No-vowel suffixes (the K8s safe alphabet): "dfnly", "5f8d9"
    - Mixed alpha+numeric suffixes: "abc123", "7b4c6d" (RS/Deployment hashes)
    """
    if not (5 <= len(suffix) <= 10 and suffix.isalnum() and suffix.islower()):
        return False
    # Pattern 1: no vowels (K8s safe alphabet)
    if not (_VOWELS & set(suffix)):
        return True
    # Pattern 2: mixed letters and digits (ReplicaSet/Deployment hashes)
    if any(c.isdigit() for c in suffix) and any(c.isalpha() for c in suffix):
        return True
    return False


def _extract_base_service_name(service_name: str) -> str:
    """
    Extract base service name from K8s workload name.

    Handles layered suffixes (pod hash + job index + cronjob timestamp)
    by stripping iteratively.  Tries structurally-unambiguous combined
    patterns first so that hash-detection failures don't cascade.

    Examples:
    - "batch-etl-job-58002-0-iquru" -> "batch-etl-job"
    - "batch-etl-job-57990-0-dfnly" -> "batch-etl-job"
    - "batch-etl-job-57993-0" -> "batch-etl-job"
    - "my-cronjob-1708123456" -> "my-cronjob"
    - "room-management-api-5f8d9" -> "room-management-api"
    - "payment-processor-v2" -> "payment-processor-v2"
    - "auth-service" -> "auth-service"
    """
    import re

    # 1. Combined Job pod: <name>-<instance>-<retry>-<pod-suffix>
    #    Structurally unambiguous — digits-digits-alphanum can only be a Job pod.
    #    No hash detection needed; any 5-10 char lowercase alnum suffix qualifies.
    m = re.match(r"^(.+)-\d+-\d+-([a-z0-9]{5,10})$", service_name)
    if m:
        service_name = m.group(1)
        # Fall through to step 4 (CronJob timestamp) — the Job
        # might have been created by a CronJob.
    else:
        # 2. Pod hash suffix only: <name>-<hash>
        #    Uses strict heuristic (no structural context to rely on).
        parts = service_name.rsplit("-", 1)
        if len(parts) == 2 and _looks_like_k8s_hash(parts[1]):
            service_name = parts[0]

        # 3. Job instance without pod hash: <name>-<instance>-<retry>
        m = re.match(r"^(.+)-\d+-\d+$", service_name)
        if m:
            service_name = m.group(1)

    # 4. CronJob timestamp: <name>-<8-10 digit unix timestamp>
    m = re.match(r"^(.+)-(\d{8,10})$", service_name)
    if m:
        service_name = m.group(1)

    return service_name


def _is_valid_repo_format(repo: str) -> bool:
    """Validate repo format is 'org/repo'."""
    if not repo or "/" not in repo:
        return False

    parts = repo.split("/")
    if len(parts) != 2:
        return False

    org, name = parts
    # Basic validation: alphanumeric, hyphens, underscores
    if not org or not name:
        return False

    return True


def _load_service_catalog() -> Dict[str, Any]:
    """Load user-maintained service catalog."""
    config_path = Path("config/service-catalog.yaml")

    if not config_path.exists():
        return {}

    try:
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _load_third_party_catalog() -> Dict[str, Any]:
    """Load third-party services catalog (shipped with agent)."""
    # First try custom catalog
    custom_path = Path("config/third-party-catalog-custom.yaml")
    if custom_path.exists():
        try:
            with open(custom_path) as f:
                catalog = yaml.safe_load(f) or {}
                if catalog:
                    return catalog
        except Exception:
            pass

    # Fall back to default catalog
    default_path = Path("config/third-party-catalog.yaml")
    if not default_path.exists():
        return {}

    try:
        with open(default_path) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def collect_github_evidence(investigation: Any) -> Dict[str, Any]:
    """
    Collect GitHub evidence: commits, workflows, docs.

    Returns dict with keys:
    - repo: "org/repo" or None
    - repo_discovery_method: How repo was found
    - is_third_party: bool
    - recent_commits: List of commits
    - workflow_runs: List of workflow runs
    - failed_workflow_logs: String or None
    - readme: String or None
    - docs: List of doc files
    - errors: List of error messages
    """
    from agent.providers.github_provider import get_github_provider

    errors: List[str] = []

    # Discover repo
    repo = discover_github_repo(investigation)
    if not repo:
        return {"errors": ["github_repo_not_found"]}

    # Determine discovery method (for observability)
    discovery_method = _determine_discovery_method(investigation, repo)

    # Check if third-party
    third_party_catalog = _load_third_party_catalog()
    is_third_party = any(
        svc.get("github_repo") == repo for svc in third_party_catalog.get("third_party_services", {}).values()
    )

    github = get_github_provider()
    time_window = investigation.time_window

    # Recent commits (2h window before alert)
    recent_commits = []
    try:
        since = time_window.start_time - timedelta(hours=2)
        until = time_window.end_time

        commits = github.get_recent_commits(repo=repo, since=since, until=until, branch="main")

        # Filter out error responses
        if commits and isinstance(commits, list) and not (len(commits) == 1 and "error" in commits[0]):
            recent_commits = commits[:10]  # Cap at 10
        elif commits and "error" in commits[0]:
            errors.append(f"commits:{commits[0].get('message', 'unknown')}")
    except Exception as e:
        errors.append(f"commits:{type(e).__name__}")

    # Workflow runs (last 5 runs since time window start)
    workflow_runs = []
    failed_logs = None
    try:
        runs = github.get_workflow_runs(repo=repo, since=time_window.start_time, limit=5)

        # Filter out error responses
        if runs and isinstance(runs, list) and not (len(runs) == 1 and "error" in runs[0]):
            workflow_runs = runs

            # Fetch logs for first failed run
            failed_run = next((r for r in runs if r.get("conclusion") == "failure"), None)
            if failed_run:
                failed_job = next((j for j in failed_run.get("jobs", []) if j.get("conclusion") == "failure"), None)
                if failed_job:
                    logs = github.get_workflow_run_logs(repo=repo, run_id=failed_run["id"], job_id=failed_job["id"])
                    if not logs.startswith("Error"):
                        failed_logs = logs
        elif runs and "error" in runs[0]:
            errors.append(f"workflows:{runs[0].get('message', 'unknown')}")
    except Exception as e:
        errors.append(f"workflows:{type(e).__name__}")

    # Documentation (README + docs/)
    readme = None
    docs = []
    try:
        readme = github.get_file_contents(repo=repo, path="README.md")
    except Exception:
        pass  # README not found is common, don't log error

    try:
        doc_files = github.list_directory(repo=repo, path="docs")
        for file in doc_files[:5]:  # Cap at 5 docs
            if file.endswith(".md"):
                try:
                    content = github.get_file_contents(repo=repo, path=f"docs/{file}")
                    docs.append({"path": f"docs/{file}", "content": content})
                except Exception:
                    pass
    except Exception:
        pass  # docs/ not found is common, don't log error

    return {
        "repo": repo,
        "repo_discovery_method": discovery_method,
        "is_third_party": is_third_party,
        "recent_commits": recent_commits,
        "workflow_runs": workflow_runs,
        "failed_workflow_logs": failed_logs,
        "readme": readme,
        "docs": docs,
        "errors": errors,
    }


def _determine_discovery_method(investigation: Any, repo: str) -> str:
    """Determine which discovery method found the repo (for observability)."""
    # Quick re-check each method to see which one matches
    if _discover_from_k8s_annotations(investigation) == repo:
        return "k8s_annotation"
    if _discover_from_alert_labels(investigation) == repo:
        return "alert_label"

    service_name = investigation.target.workload_name or investigation.target.pod or ""
    if _discover_from_service_catalog(service_name) == repo:
        return "service_catalog"
    if _discover_from_third_party_catalog(service_name) == repo:
        return "third_party_catalog"
    if _discover_from_naming_convention(service_name) == repo:
        return "naming_convention"

    return "unknown"
