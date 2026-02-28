"""
Tests for GitHub chat tools: resolution, commit_diff, observability, RCA registration.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

from agent.authz.policy import ChatPolicy
from agent.chat.tools import (
    _discover_repo_for_chat,
    _resolve_github_repo,
    run_tool,
)


def _make_policy(**overrides) -> ChatPolicy:
    """Build a ChatPolicy with GitHub enabled by default."""
    defaults = dict(
        enabled=True,
        allow_github_read=True,
        github_repo_allowlist=None,
    )
    defaults.update(overrides)
    return ChatPolicy(**defaults)


def _make_analysis_json(
    *,
    github_repo: Optional[str] = None,
    workload_name: Optional[str] = None,
    alert_labels: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Build a minimal analysis_json dict for testing."""
    aj: Dict[str, Any] = {"target": {}, "evidence": {}, "alert": {"labels": alert_labels or {}}}
    if github_repo:
        aj["evidence"]["github"] = {"repo": github_repo}
    if workload_name:
        aj["target"]["workload_name"] = workload_name
    return aj


# ---------------------------------------------------------------------------
# A. Resolution tests
# ---------------------------------------------------------------------------


class TestResolveGithubRepo:
    def test_valid_org_repo_from_args(self):
        """LLM passes a valid org/repo → returns (repo, 'args')."""
        repo, source = _resolve_github_repo({"repo": "myorg/myrepo"}, {})
        assert repo == "myorg/myrepo"
        assert source == "args"

    def test_bare_name_via_service_catalog(self):
        """Bare workload name resolved through service catalog → returns (repo, 'service_catalog')."""
        with patch(
            "agent.collectors.github_context._discover_from_service_catalog",
            return_value="acme/order-processing-service",
        ):
            repo, source = _resolve_github_repo(
                {"repo": "order-processing-service"},
                _make_analysis_json(),
            )
            assert repo == "acme/order-processing-service"
            assert source == "service_catalog"

    def test_bare_name_via_naming_convention(self, monkeypatch):
        """Bare name resolved via GITHUB_DEFAULT_ORG naming convention."""
        monkeypatch.setenv("GITHUB_DEFAULT_ORG", "acme")
        with patch(
            "agent.collectors.github_context._discover_from_service_catalog",
            return_value=None,
        ):
            repo, source = _resolve_github_repo(
                {"repo": "my-service"},
                _make_analysis_json(),
            )
            assert repo == "acme/my-service"
            assert source == "naming_convention"

    def test_not_found(self, monkeypatch):
        """No repo discovered → returns (None, 'not_found')."""
        monkeypatch.delenv("GITHUB_DEFAULT_ORG", raising=False)
        with patch(
            "agent.collectors.github_context._discover_from_service_catalog",
            return_value=None,
        ):
            repo, source = _resolve_github_repo({"repo": ""}, _make_analysis_json())
            assert repo is None
            assert source == "not_found"


class TestResolveGithubRepoJobSuffix:
    """LLM passes a Job pod name as org/repo — must strip K8s instance suffix."""

    def test_job_pod_name_stripped_and_resolved_via_catalog(self):
        """org/job-57992-0 → strip suffix → discover via service catalog."""
        with patch(
            "agent.collectors.github_context._discover_from_service_catalog",
            return_value="myorg/order-processing-service",
        ):
            repo, source = _resolve_github_repo(
                {"repo": "myorg/batch-etl-job-57992-0"},
                _make_analysis_json(),
            )
        assert repo == "myorg/order-processing-service"
        assert source == "service_catalog"

    def test_job_pod_name_stripped_fallback_to_cleaned(self, monkeypatch):
        """org/job-57992-0 → strip suffix → catalog misses → falls back to cleaned name."""
        monkeypatch.delenv("GITHUB_DEFAULT_ORG", raising=False)
        with patch(
            "agent.collectors.github_context._discover_from_service_catalog",
            return_value=None,
        ):
            repo, source = _resolve_github_repo(
                {"repo": "myorg/batch-etl-job-57992-0"},
                _make_analysis_json(),
            )
        assert repo == "myorg/batch-etl-job"
        assert source == "args_cleaned"

    def test_normal_org_repo_not_affected(self):
        """Normal org/repo (no K8s suffix) passes through unchanged."""
        repo, source = _resolve_github_repo(
            {"repo": "myorg/order-processing-service"},
            _make_analysis_json(),
        )
        assert repo == "myorg/order-processing-service"
        assert source == "args"


class TestDiscoverRepoForChat:
    def test_evidence_github(self):
        """Repo found in evidence.github → source is 'evidence.github'."""
        aj = _make_analysis_json(github_repo="org/from-evidence")
        repo, source = _discover_repo_for_chat(aj)
        assert repo == "org/from-evidence"
        assert source == "evidence.github"

    def test_alert_labels(self):
        """Repo found in alert labels → source is 'alert_labels'."""
        aj = _make_analysis_json(alert_labels={"github_repo": "org/from-labels"})
        repo, source = _discover_repo_for_chat(aj)
        assert repo == "org/from-labels"
        assert source == "alert_labels"


# ---------------------------------------------------------------------------
# B. commit_diff tests
# ---------------------------------------------------------------------------


class TestCommitDiff:
    def test_returns_files_and_patch(self):
        """commit_diff returns files list and patch from provider."""
        mock_provider = MagicMock()
        mock_provider.get_commit_diff.return_value = {
            "sha": "abc1234",
            "message": "Upgrade AWS SDK",
            "files": [
                {
                    "filename": "pom.xml",
                    "status": "modified",
                    "additions": 5,
                    "deletions": 3,
                    "patch": "@@ -10,3 +10,5 @@ ...",
                },
            ],
            "stats": {"total": 8, "additions": 5, "deletions": 3},
        }

        policy = _make_policy()
        aj = _make_analysis_json(github_repo="acme/myrepo")

        with patch("agent.providers.github_provider.get_github_provider", return_value=mock_provider):
            result = run_tool(
                policy=policy,
                action_policy=None,
                tool="github.commit_diff",
                args={"sha": "abc1234"},
                analysis_json=aj,
            )

        assert result.ok is True
        assert isinstance(result.result, dict)
        assert result.result["sha"] == "abc1234"
        assert len(result.result["files"]) == 1
        assert result.result["files"][0]["filename"] == "pom.xml"

    def test_caps_patch_length(self):
        """Provider caps patch at 500 chars per file."""
        from agent.providers.github_provider import DefaultGitHubProvider

        provider = DefaultGitHubProvider()
        provider._installation_token = "ghs_test"
        provider._token_expires_at = datetime.now(timezone.utc).replace(hour=23, minute=59)

        long_patch = "x" * 1000
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "sha": "abc123def456",
            "commit": {"message": "test"},
            "files": [
                {
                    "filename": "big.py",
                    "status": "modified",
                    "additions": 100,
                    "deletions": 50,
                    "patch": long_patch,
                }
            ],
            "stats": {"total": 150, "additions": 100, "deletions": 50},
        }

        with patch.object(provider, "_make_request", return_value=mock_response):
            result = provider.get_commit_diff("org/repo", "abc123")

        assert len(result["files"]) == 1
        patch_text = result["files"][0]["patch"]
        assert len(patch_text) == 503  # 500 chars + "..."
        assert patch_text.endswith("...")

    def test_sha_required(self):
        """Error when no SHA provided."""
        policy = _make_policy()
        result = run_tool(
            policy=policy,
            action_policy=None,
            tool="github.commit_diff",
            args={},
            analysis_json=_make_analysis_json(github_repo="acme/repo"),
        )
        assert result.ok is False
        assert result.error == "sha_required"


# ---------------------------------------------------------------------------
# C. Observability tests
# ---------------------------------------------------------------------------


class TestObservability:
    def test_repo_not_discovered_logs_warning(self, caplog, monkeypatch):
        """WARNING is logged when repo cannot be discovered."""
        monkeypatch.delenv("GITHUB_DEFAULT_ORG", raising=False)
        policy = _make_policy()
        with patch(
            "agent.collectors.github_context._discover_from_service_catalog",
            return_value=None,
        ):
            with caplog.at_level(logging.WARNING, logger="agent.chat.tools"):
                result = run_tool(
                    policy=policy,
                    action_policy=None,
                    tool="github.recent_commits",
                    args={"repo": "bare-name-no-org"},
                    analysis_json=_make_analysis_json(),
                )
        assert result.ok is False
        assert "repo_not_discovered" in (result.error or "")
        # Check that a WARNING was logged
        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("repo not discovered" in m for m in warning_msgs)

    def test_provider_error_includes_message(self):
        """Provider error dict is surfaced with HTTP message in error string."""
        mock_provider = MagicMock()
        mock_provider.get_recent_commits.return_value = [
            {"error": "github_error:HTTPError", "message": "404 Not Found"}
        ]

        policy = _make_policy()
        aj = _make_analysis_json(github_repo="acme/does-not-exist")

        with patch("agent.providers.github_provider.get_github_provider", return_value=mock_provider):
            result = run_tool(
                policy=policy,
                action_policy=None,
                tool="github.recent_commits",
                args={},
                analysis_json=aj,
            )

        assert result.ok is False
        assert "404 Not Found" in (result.error or "")


# ---------------------------------------------------------------------------
# D. Limit cap and auto-widen tests
# ---------------------------------------------------------------------------


class TestRecentCommitsLimitAndAutoWiden:
    """Verify limit is capped and empty results trigger auto-widen."""

    def _run_recent_commits(self, mock_provider, args=None):
        policy = _make_policy()
        aj = _make_analysis_json(github_repo="acme/myrepo")
        with patch("agent.providers.github_provider.get_github_provider", return_value=mock_provider):
            return run_tool(
                policy=policy,
                action_policy=None,
                tool="github.recent_commits",
                args=args or {},
                analysis_json=aj,
            )

    def test_limit_capped_at_30(self):
        """LLM passes limit=500 → tool caps at 30."""
        fake_commits = [{"sha": f"abc{i}", "message": f"commit {i}"} for i in range(100)]
        mock_provider = MagicMock()
        mock_provider.get_recent_commits.return_value = fake_commits

        result = self._run_recent_commits(mock_provider, args={"limit": 500})

        assert result.ok is True
        assert result.result["returned"] == 30
        assert result.result["total_available"] == 100

    def test_default_limit_is_20(self):
        """No limit arg → default 20 returned."""
        fake_commits = [{"sha": f"abc{i}", "message": f"commit {i}"} for i in range(50)]
        mock_provider = MagicMock()
        mock_provider.get_recent_commits.return_value = fake_commits

        result = self._run_recent_commits(mock_provider, args={})

        assert result.ok is True
        assert result.result["returned"] == 20
        assert result.result["total_available"] == 50

    def test_auto_widens_to_24h_on_empty(self):
        """0 commits in default 2h window → auto-retries with 24h."""
        mock_provider = MagicMock()
        # First call (2h) returns empty, second call (24h) returns commits
        mock_provider.get_recent_commits.side_effect = [
            [],  # 2h window: empty
            [{"sha": "abc1", "message": "old commit"}],  # 24h window: found
        ]

        result = self._run_recent_commits(mock_provider, args={})

        assert result.ok is True
        assert result.result["returned"] == 1
        assert result.result["searched_window_hours"] == 24
        # Provider called twice (2h then 24h)
        assert mock_provider.get_recent_commits.call_count == 2

    def test_no_auto_widen_when_since_specified(self):
        """Explicit since arg → no auto-widen even if 0 results."""
        mock_provider = MagicMock()
        mock_provider.get_recent_commits.return_value = []

        result = self._run_recent_commits(
            mock_provider,
            args={"since": "2026-02-24T08:00:00Z"},
        )

        assert result.ok is True
        assert result.result["returned"] == 0
        # Provider called only once (no auto-widen)
        assert mock_provider.get_recent_commits.call_count == 1

    def test_result_includes_searched_window(self):
        """Result always includes searched_window_hours."""
        mock_provider = MagicMock()
        mock_provider.get_recent_commits.return_value = [{"sha": "abc", "message": "hi"}]

        result = self._run_recent_commits(mock_provider, args={})

        assert result.ok is True
        assert result.result["searched_window_hours"] == 2


# ---------------------------------------------------------------------------
# E. RCA graph registration
# ---------------------------------------------------------------------------


class TestRCAGraphGitHub:
    def test_rca_allowed_tools_includes_github(self):
        """RCA graph's _allowed_tools includes GitHub tools when policy allows."""
        from agent.graphs.rca import _allowed_tools

        policy = ChatPolicy(enabled=True, allow_github_read=True)
        tools = _allowed_tools(policy)

        expected = [
            "github.recent_commits",
            "github.workflow_runs",
            "github.workflow_logs",
            "github.read_file",
            "github.commit_diff",
        ]
        for t in expected:
            assert t in tools, f"{t} not found in RCA allowed_tools"

    def test_rca_no_github_when_disabled(self):
        """RCA graph excludes GitHub tools when policy disables them."""
        from agent.graphs.rca import _allowed_tools

        policy = ChatPolicy(enabled=True, allow_github_read=False)
        tools = _allowed_tools(policy)

        assert not any(t.startswith("github.") for t in tools)
