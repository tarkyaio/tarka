"""
Unit tests for GitHub evidence collector.
"""

from __future__ import annotations

# Import unittest.mock for mock_open
import unittest.mock
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from agent.collectors.github_context import collect_github_evidence


def _mock_investigation(
    workload_name: str = "test-service",
    namespace: str = "default",
    time_window_hours: int = 2,
):
    """Create mock investigation for testing."""
    inv = MagicMock()
    inv.target.workload_name = workload_name
    inv.target.pod = workload_name + "-abc123"
    inv.target.namespace = namespace

    inv.alert.labels = {}
    inv.evidence.k8s.owner_chain = None

    # Time window
    now = datetime.now(timezone.utc)
    inv.time_window.start_time = now - timedelta(hours=time_window_hours)
    inv.time_window.end_time = now

    return inv


def test_collect_github_evidence_repo_not_found(monkeypatch):
    """Collector returns error if repo cannot be discovered."""
    monkeypatch.delenv("GITHUB_DEFAULT_ORG", raising=False)

    inv = _mock_investigation(workload_name="unknown-service")

    with patch("pathlib.Path.exists", return_value=False):
        result = collect_github_evidence(inv)

    assert "errors" in result
    assert "github_repo_not_found" in result["errors"]


def test_collect_github_evidence_successful(monkeypatch):
    """Collector successfully retrieves GitHub evidence."""
    monkeypatch.setenv("GITHUB_DEFAULT_ORG", "myorg")

    inv = _mock_investigation(workload_name="test-service")

    # Mock GitHub provider
    mock_provider = MagicMock()

    # Mock commits
    mock_provider.get_recent_commits.return_value = [
        {
            "sha": "abc123d",
            "author": "Alice",
            "message": "fix: resolve bug",
            "timestamp": "2026-02-18T10:00:00Z",
            "url": "https://github.com/myorg/test-service/commit/abc123",
        }
    ]

    # Mock workflow runs
    mock_provider.get_workflow_runs.return_value = [
        {
            "id": 12345,
            "workflow_name": "CI",
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-02-18T10:00:00Z",
            "updated_at": "2026-02-18T10:05:00Z",
            "url": "https://github.com/myorg/test-service/actions/runs/12345",
            "jobs": [{"id": 101, "name": "build", "status": "completed", "conclusion": "success"}],
        }
    ]

    # Mock README
    mock_provider.get_file_contents.return_value = "# Test Service\n\nThis is a test."

    # Mock docs
    mock_provider.list_directory.return_value = ["setup.md"]

    with patch("agent.providers.github_provider.get_github_provider", return_value=mock_provider):
        result = collect_github_evidence(inv)

    assert result["repo"] == "myorg/test-service"
    assert result["repo_discovery_method"] == "naming_convention"
    assert result["is_third_party"] is False
    assert len(result["recent_commits"]) == 1
    assert result["recent_commits"][0]["author"] == "Alice"
    assert len(result["workflow_runs"]) == 1
    assert result["workflow_runs"][0]["workflow_name"] == "CI"
    assert result["readme"] == "# Test Service\n\nThis is a test."
    assert len(result["docs"]) == 1


def test_collect_github_evidence_failed_workflow_logs(monkeypatch):
    """Collector fetches logs for failed workflows."""
    monkeypatch.setenv("GITHUB_DEFAULT_ORG", "myorg")

    inv = _mock_investigation(workload_name="test-service")

    mock_provider = MagicMock()
    mock_provider.get_recent_commits.return_value = []

    # Mock workflow with failure
    mock_provider.get_workflow_runs.return_value = [
        {
            "id": 12345,
            "workflow_name": "CI",
            "status": "completed",
            "conclusion": "failure",
            "created_at": "2026-02-18T10:00:00Z",
            "updated_at": "2026-02-18T10:05:00Z",
            "url": "https://github.com/myorg/test-service/actions/runs/12345",
            "jobs": [{"id": 101, "name": "build", "status": "completed", "conclusion": "failure"}],
        }
    ]

    # Mock failed job logs
    mock_provider.get_workflow_run_logs.return_value = "Build failed: missing dependency\nStack trace..."

    mock_provider.get_file_contents.side_effect = Exception("Not found")
    mock_provider.list_directory.side_effect = Exception("Not found")

    with patch("agent.providers.github_provider.get_github_provider", return_value=mock_provider):
        result = collect_github_evidence(inv)

    assert result["failed_workflow_logs"] == "Build failed: missing dependency\nStack trace..."
    assert result["workflow_runs"][0]["conclusion"] == "failure"


def test_collect_github_evidence_third_party_detection(monkeypatch):
    """Collector detects third-party services."""
    monkeypatch.delenv("GITHUB_DEFAULT_ORG", raising=False)

    inv = _mock_investigation(workload_name="coredns")

    mock_provider = MagicMock()
    mock_provider.get_recent_commits.return_value = []
    mock_provider.get_workflow_runs.return_value = []
    mock_provider.get_file_contents.side_effect = Exception("Not found")
    mock_provider.list_directory.side_effect = Exception("Not found")

    # Mock third-party catalog
    catalog_content = """
third_party_services:
  coredns:
    github_repo: "coredns/coredns"
"""

    with patch("agent.providers.github_provider.get_github_provider", return_value=mock_provider):
        with patch("builtins.open", unittest.mock.mock_open(read_data=catalog_content)):
            with patch("pathlib.Path.exists", return_value=True):
                result = collect_github_evidence(inv)

    assert result["repo"] == "coredns/coredns"
    assert result["is_third_party"] is True


def test_collect_github_evidence_handles_commit_errors(monkeypatch):
    """Collector handles commit fetch errors gracefully."""
    monkeypatch.setenv("GITHUB_DEFAULT_ORG", "myorg")

    inv = _mock_investigation(workload_name="test-service")

    mock_provider = MagicMock()
    mock_provider.get_recent_commits.side_effect = Exception("API error")
    mock_provider.get_workflow_runs.return_value = []
    mock_provider.get_file_contents.side_effect = Exception("Not found")
    mock_provider.list_directory.side_effect = Exception("Not found")

    with patch("agent.providers.github_provider.get_github_provider", return_value=mock_provider):
        result = collect_github_evidence(inv)

    assert result["repo"] == "myorg/test-service"
    assert len(result["recent_commits"]) == 0
    assert any("commits" in err for err in result["errors"])


def test_collect_github_evidence_handles_workflow_errors(monkeypatch):
    """Collector handles workflow fetch errors gracefully."""
    monkeypatch.setenv("GITHUB_DEFAULT_ORG", "myorg")

    inv = _mock_investigation(workload_name="test-service")

    mock_provider = MagicMock()
    mock_provider.get_recent_commits.return_value = []
    mock_provider.get_workflow_runs.side_effect = Exception("API error")
    mock_provider.get_file_contents.side_effect = Exception("Not found")
    mock_provider.list_directory.side_effect = Exception("Not found")

    with patch("agent.providers.github_provider.get_github_provider", return_value=mock_provider):
        result = collect_github_evidence(inv)

    assert result["repo"] == "myorg/test-service"
    assert len(result["workflow_runs"]) == 0
    assert any("workflows" in err for err in result["errors"])


def test_collect_github_evidence_missing_readme_is_not_error(monkeypatch):
    """Collector treats missing README as normal (not an error)."""
    monkeypatch.setenv("GITHUB_DEFAULT_ORG", "myorg")

    inv = _mock_investigation(workload_name="test-service")

    mock_provider = MagicMock()
    mock_provider.get_recent_commits.return_value = []
    mock_provider.get_workflow_runs.return_value = []
    mock_provider.get_file_contents.side_effect = Exception("Not found")
    mock_provider.list_directory.side_effect = Exception("Not found")

    with patch("agent.providers.github_provider.get_github_provider", return_value=mock_provider):
        result = collect_github_evidence(inv)

    assert result["readme"] is None
    assert len(result["docs"]) == 0
    # No errors for missing docs
    assert not any("readme" in err.lower() for err in result["errors"])


def test_collect_github_evidence_caps_commits_at_10(monkeypatch):
    """Collector caps commits at 10 to avoid excessive data."""
    monkeypatch.setenv("GITHUB_DEFAULT_ORG", "myorg")

    inv = _mock_investigation(workload_name="test-service")

    mock_provider = MagicMock()

    # Return 20 commits
    mock_provider.get_recent_commits.return_value = [
        {"sha": f"commit{i}", "author": "Alice", "message": f"Commit {i}"} for i in range(20)
    ]

    mock_provider.get_workflow_runs.return_value = []
    mock_provider.get_file_contents.side_effect = Exception("Not found")
    mock_provider.list_directory.side_effect = Exception("Not found")

    with patch("agent.providers.github_provider.get_github_provider", return_value=mock_provider):
        result = collect_github_evidence(inv)

    # Should cap at 10
    assert len(result["recent_commits"]) == 10


def test_collect_github_evidence_caps_docs_at_5(monkeypatch):
    """Collector caps docs at 5 to avoid excessive data."""
    monkeypatch.setenv("GITHUB_DEFAULT_ORG", "myorg")

    inv = _mock_investigation(workload_name="test-service")

    mock_provider = MagicMock()
    mock_provider.get_recent_commits.return_value = []
    mock_provider.get_workflow_runs.return_value = []
    mock_provider.get_file_contents.return_value = "Doc content"

    # Return 10 doc files
    mock_provider.list_directory.return_value = [f"doc{i}.md" for i in range(10)]

    with patch("agent.providers.github_provider.get_github_provider", return_value=mock_provider):
        result = collect_github_evidence(inv)

    # Should cap at 5
    assert len(result["docs"]) == 5
