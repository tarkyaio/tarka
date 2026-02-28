"""
Unit tests for GitHub service discovery (8-step fallback chain).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

from agent.collectors.github_context import (
    _discover_from_alert_labels,
    _discover_from_k8s_annotations,
    _discover_from_naming_convention,
    _discover_from_service_catalog,
    _discover_from_third_party_catalog,
    _extract_base_service_name,
    _is_valid_repo_format,
    discover_github_repo,
)


def _mock_investigation(
    workload_name: str = "test-service",
    namespace: str = "default",
    alert_labels: dict = None,
    k8s_annotations: dict = None,
):
    """Create mock investigation for testing."""
    inv = MagicMock()
    inv.target.workload_name = workload_name
    inv.target.pod = workload_name + "-abc123"
    inv.target.namespace = namespace

    inv.alert.labels = alert_labels or {}

    inv.evidence.k8s.owner_chain = {"annotations": k8s_annotations or {}}

    return inv


def test_discover_from_k8s_annotations_standard_key():
    """Step 1: Discover from K8s annotation (github.com/repo)."""
    inv = _mock_investigation(k8s_annotations={"github.com/repo": "myorg/my-service"})

    repo = _discover_from_k8s_annotations(inv)

    assert repo == "myorg/my-service"


def test_discover_from_k8s_annotations_custom_key():
    """Step 1: Discover from K8s annotation (tarka.io/github-repo)."""
    inv = _mock_investigation(k8s_annotations={"tarka.io/github-repo": "myorg/custom-service"})

    repo = _discover_from_k8s_annotations(inv)

    assert repo == "myorg/custom-service"


def test_discover_from_k8s_annotations_no_owner_chain():
    """Step 1: Handle missing owner chain gracefully."""
    inv = _mock_investigation()
    inv.evidence.k8s.owner_chain = None

    repo = _discover_from_k8s_annotations(inv)

    assert repo is None


def test_discover_from_alert_labels():
    """Step 2: Discover from Prometheus alert label."""
    inv = _mock_investigation(alert_labels={"github_repo": "myorg/alert-service"})

    repo = _discover_from_alert_labels(inv)

    assert repo == "myorg/alert-service"


def test_discover_from_alert_labels_alternative_key():
    """Step 2: Discover from alternative label key."""
    inv = _mock_investigation(alert_labels={"github_repository": "myorg/other-service"})

    repo = _discover_from_alert_labels(inv)

    assert repo == "myorg/other-service"


def test_discover_from_naming_convention(monkeypatch):
    """Step 3: Discover using naming convention."""
    monkeypatch.setenv("GITHUB_DEFAULT_ORG", "myorg")
    inv = _mock_investigation(workload_name="payment-service")

    repo = _discover_from_naming_convention(inv.target.workload_name)

    assert repo == "myorg/payment-service"


def test_discover_from_naming_convention_strips_pod_hash(monkeypatch):
    """Step 3: Naming convention strips pod hash suffix."""
    monkeypatch.setenv("GITHUB_DEFAULT_ORG", "myorg")

    repo = _discover_from_naming_convention("auth-service-abc123")

    assert repo == "myorg/auth-service"


def test_discover_from_naming_convention_no_org(monkeypatch):
    """Step 3: Returns None if GITHUB_DEFAULT_ORG not set."""
    monkeypatch.delenv("GITHUB_DEFAULT_ORG", raising=False)

    repo = _discover_from_naming_convention("test-service")

    assert repo is None


def test_discover_from_service_catalog():
    """Step 4: Discover from service catalog YAML."""
    catalog_content = """
services:
  room-management-api:
    github_repo: "myorg/room-management-service"
    team: "booking-team"

  payment-processor:
    github_repo: "myorg/payment-service"
"""

    with patch("builtins.open", mock_open(read_data=catalog_content)):
        with patch("pathlib.Path.exists", return_value=True):
            repo = _discover_from_service_catalog("room-management-api")

    assert repo == "myorg/room-management-service"


def test_discover_from_service_catalog_strips_hash():
    """Step 4: Catalog lookup strips pod hash."""
    catalog_content = """
services:
  auth-service:
    github_repo: "myorg/authentication"
"""

    with patch("builtins.open", mock_open(read_data=catalog_content)):
        with patch("pathlib.Path.exists", return_value=True):
            repo = _discover_from_service_catalog("auth-service-xyz789")

    assert repo == "myorg/authentication"


def test_discover_from_service_catalog_executor_suffix():
    """Step 4: Catalog fuzzy match strips -executor suffix and tries -service variant."""
    catalog_content = """
services:
  order-processing-service:
    github_repo: "myorg/order-processing-service"
"""

    with patch("builtins.open", mock_open(read_data=catalog_content)):
        with patch("pathlib.Path.exists", return_value=True):
            repo = _discover_from_service_catalog("order-processing-service-executor")

    assert repo == "myorg/order-processing-service"


def test_discover_from_service_catalog_handler_suffix():
    """Step 4: Catalog fuzzy match strips -handler suffix."""
    catalog_content = """
services:
  event-processor:
    github_repo: "myorg/event-processor"
"""

    with patch("builtins.open", mock_open(read_data=catalog_content)):
        with patch("pathlib.Path.exists", return_value=True):
            repo = _discover_from_service_catalog("event-processor-handler")

    assert repo == "myorg/event-processor"


def test_discover_from_service_catalog_missing_file():
    """Step 4: Returns None if catalog file doesn't exist."""
    with patch("pathlib.Path.exists", return_value=False):
        repo = _discover_from_service_catalog("test-service")

    assert repo is None


def test_discover_from_third_party_catalog():
    """Step 7: Discover from third-party catalog."""
    catalog_content = """
third_party_services:
  coredns:
    github_repo: "coredns/coredns"
    docs_url: "https://coredns.io/manual/toc/"
    category: "infrastructure"

  cert-manager:
    github_repo: "cert-manager/cert-manager"
"""

    with patch("builtins.open", mock_open(read_data=catalog_content)):
        with patch("pathlib.Path.exists", return_value=True):
            repo = _discover_from_third_party_catalog("coredns")

    assert repo == "coredns/coredns"


def test_discover_from_third_party_catalog_case_insensitive():
    """Step 7: Third-party lookup is case-insensitive."""
    catalog_content = """
third_party_services:
  coredns:
    github_repo: "coredns/coredns"
"""

    with patch("builtins.open", mock_open(read_data=catalog_content)):
        with patch("pathlib.Path.exists", return_value=True):
            repo = _discover_from_third_party_catalog("CoreDNS")

    assert repo == "coredns/coredns"


def test_discover_from_third_party_catalog_custom_override():
    """Step 7: Custom catalog overrides default."""
    custom_content = """
third_party_services:
  coredns:
    github_repo: "custom-fork/coredns"
"""

    # Mock Path.exists() to return True only for custom catalog
    def mock_exists(self):
        return str(self).endswith("custom.yaml")

    with patch("builtins.open", mock_open(read_data=custom_content)):
        with patch.object(Path, "exists", mock_exists):
            repo = _discover_from_third_party_catalog("coredns")

    assert repo == "custom-fork/coredns"


def test_discover_github_repo_priority_k8s_annotations(monkeypatch):
    """Discovery chain: K8s annotations take priority."""
    monkeypatch.setenv("GITHUB_DEFAULT_ORG", "myorg")

    inv = _mock_investigation(
        workload_name="test-service",
        k8s_annotations={"github.com/repo": "priority-org/annotation-service"},
        alert_labels={"github_repo": "other-org/alert-service"},
    )

    repo = discover_github_repo(inv)

    assert repo == "priority-org/annotation-service"


def test_discover_github_repo_fallback_to_alert_labels(monkeypatch):
    """Discovery chain: Falls back to alert labels."""
    monkeypatch.setenv("GITHUB_DEFAULT_ORG", "myorg")

    inv = _mock_investigation(workload_name="test-service", alert_labels={"github_repo": "myorg/alert-service"})
    inv.evidence.k8s.owner_chain = None  # No K8s annotations

    repo = discover_github_repo(inv)

    assert repo == "myorg/alert-service"


def test_discover_github_repo_fallback_to_naming_convention(monkeypatch):
    """Discovery chain: Falls back to naming convention."""
    monkeypatch.setenv("GITHUB_DEFAULT_ORG", "myorg")

    inv = _mock_investigation(workload_name="payment-service")
    inv.evidence.k8s.owner_chain = None

    repo = discover_github_repo(inv)

    assert repo == "myorg/payment-service"


def test_discover_github_repo_fallback_to_service_catalog(monkeypatch):
    """Discovery chain: Falls back to service catalog."""
    monkeypatch.delenv("GITHUB_DEFAULT_ORG", raising=False)

    catalog_content = """
services:
  special-service:
    github_repo: "custom-org/special-repo"
"""

    inv = _mock_investigation(workload_name="special-service")
    inv.evidence.k8s.owner_chain = None

    with patch("builtins.open", mock_open(read_data=catalog_content)):
        with patch("pathlib.Path.exists", return_value=True):
            repo = discover_github_repo(inv)

    assert repo == "custom-org/special-repo"


def test_discover_github_repo_fallback_to_third_party(monkeypatch):
    """Discovery chain: Falls back to third-party catalog."""
    monkeypatch.delenv("GITHUB_DEFAULT_ORG", raising=False)

    third_party_content = """
third_party_services:
  coredns:
    github_repo: "coredns/coredns"
"""

    inv = _mock_investigation(workload_name="coredns")
    inv.evidence.k8s.owner_chain = None

    # Mock Path.exists() - service catalog doesn't exist, third-party does
    def mock_exists(self):
        return "third-party" in str(self)

    with patch("builtins.open", mock_open(read_data=third_party_content)):
        with patch.object(Path, "exists", mock_exists):
            repo = discover_github_repo(inv)

    assert repo == "coredns/coredns"


def test_discover_github_repo_returns_none_if_all_fail(monkeypatch):
    """Discovery chain: Returns None if all methods fail."""
    monkeypatch.delenv("GITHUB_DEFAULT_ORG", raising=False)

    inv = _mock_investigation(workload_name="unknown-service")
    inv.evidence.k8s.owner_chain = None

    with patch("pathlib.Path.exists", return_value=False):
        repo = discover_github_repo(inv)

    assert repo is None


def test_extract_base_service_name():
    """Helper: Extract base service name from pod name."""
    assert _extract_base_service_name("auth-service-abc123") == "auth-service"
    assert _extract_base_service_name("payment-v2-xyz789") == "payment-v2"
    assert _extract_base_service_name("simple-service") == "simple-service"
    assert _extract_base_service_name("long-service-name-with-many-parts-5f8d9a") == "long-service-name-with-many-parts"
    # Short hashes or pure words are not stripped
    assert _extract_base_service_name("service-abc") == "service-abc"
    assert _extract_base_service_name("api-v2") == "api-v2"


def test_extract_base_service_name_combined_job_pod():
    """Helper: Combined Job pod pattern strips instance+retry+hash in one shot.

    This handles suffixes like 'iquru' (all-alpha with vowels) that bypass
    the _looks_like_k8s_hash heuristic.  The combined pattern is
    structurally unambiguous so no hash detection is needed.
    """
    # Production bug: all-alpha suffix with vowels bypassed hash heuristic
    assert _extract_base_service_name("batch-etl-job-58002-0-iquru") == "batch-etl-job"
    # All-alpha suffix (no digits)
    assert _extract_base_service_name("my-job-12345-0-abcde") == "my-job"
    # Mixed suffix, short instance IDs
    assert _extract_base_service_name("my-job-99-2-x7k9m") == "my-job"
    # Suffix that would pass hash heuristic anyway (still works via combined pattern)
    assert _extract_base_service_name("my-job-57990-0-dfnly") == "my-job"


def test_extract_base_service_name_cronjob_spawned_job_pod():
    """Helper: CronJob-spawned Job pod strips all layers.

    Pod name: <cronjob>-<timestamp>-<instance>-<retry>-<hash>
    Step 1 (combined) strips instance+retry+hash → <cronjob>-<timestamp>
    Step 4 (CronJob timestamp) strips timestamp → <cronjob>
    """
    assert _extract_base_service_name("cronjob-1708123456-0-bqrzw") == "cronjob"
    assert _extract_base_service_name("my-batch-1708199999-0-x7k9m") == "my-batch"


def test_is_valid_repo_format():
    """Helper: Validate repo format."""
    assert _is_valid_repo_format("myorg/myrepo") is True
    assert _is_valid_repo_format("github-org/kebab-case-repo") is True
    assert _is_valid_repo_format("invalid") is False
    assert _is_valid_repo_format("too/many/slashes") is False
    assert _is_valid_repo_format("") is False
    assert _is_valid_repo_format("/no-org") is False
    assert _is_valid_repo_format("no-repo/") is False
