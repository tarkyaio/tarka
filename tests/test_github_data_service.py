from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from agent.services.github_data_service import GitHubDataService


def test_read_file_uses_mirror_when_available():
    svc = GitHubDataService()
    cache = MagicMock()
    cache.ensure_mirror.return_value = Path("/tmp/tarka/git/acme/repo.git")
    cache.read_file.return_value = "local content"

    provider = MagicMock()
    provider.get_file_contents.side_effect = AssertionError("REST should not be called")

    with patch("agent.providers.git_mirror_provider.get_git_mirror_cache", return_value=cache):
        with patch("agent.providers.github_provider.get_github_provider", return_value=provider):
            out = svc.read_file(repo="acme/repo", path="README.md", ref="main")

    assert out["source"] == "mirror"
    assert out["content"] == "local content"


def test_read_file_falls_back_to_rest_on_mirror_error():
    svc = GitHubDataService()
    cache = MagicMock()
    cache.ensure_mirror.side_effect = RuntimeError("clone failed")

    provider = MagicMock()
    provider.get_file_contents.return_value = "rest content"

    with patch("agent.providers.git_mirror_provider.get_git_mirror_cache", return_value=cache):
        with patch("agent.providers.github_provider.get_github_provider", return_value=provider):
            out = svc.read_file(repo="acme/repo", path="README.md", ref="main")

    assert out["source"] == "rest"
    assert out["content"] == "rest content"
    assert out.get("fallback_from") == "mirror"


def test_read_file_returns_error_when_mirror_and_rest_fail():
    svc = GitHubDataService()
    cache = MagicMock()
    cache.ensure_mirror.side_effect = RuntimeError("clone failed")

    provider = MagicMock()
    provider.get_file_contents.side_effect = Exception("404")

    with patch("agent.providers.git_mirror_provider.get_git_mirror_cache", return_value=cache):
        with patch("agent.providers.github_provider.get_github_provider", return_value=provider):
            out = svc.read_file(repo="acme/repo", path="README.md", ref="main")

    assert out["source"] == "none"
    assert "error" in out


def test_commit_diff_uses_mirror_shape():
    svc = GitHubDataService()
    cache = MagicMock()
    cache.ensure_mirror.return_value = Path("/tmp/tarka/git/acme/repo.git")

    def run_git_side_effect(_path, args, **_kwargs):
        argv = list(args)
        if argv == ["rev-parse", "--short=7", "abc123"]:
            return "abc1234\n"
        if argv == ["show", "-s", "--format=%B", "abc123"]:
            return "Fix regression\n\nextra details"
        if argv == ["show", "--name-status", "--format=", "abc123"]:
            return "M\tsrc/app.py\n"
        if argv == ["show", "--numstat", "--format=", "abc123"]:
            return "10\t2\tsrc/app.py\n"
        if argv == ["show", "--format=", "--patch", "abc123"]:
            return "\n".join(
                [
                    "diff --git a/src/app.py b/src/app.py",
                    "--- a/src/app.py",
                    "+++ b/src/app.py",
                    "@@ -1,2 +1,2 @@",
                    "-old",
                    "+new",
                ]
            )
        return ""

    cache.run_git.side_effect = run_git_side_effect

    with patch("agent.providers.git_mirror_provider.get_git_mirror_cache", return_value=cache):
        out = svc.commit_diff(repo="acme/repo", sha="abc123")

    assert out["source"] == "mirror"
    diff = out["diff"]
    assert diff["sha"] == "abc1234"
    assert diff["files"][0]["filename"] == "src/app.py"
    assert diff["files"][0]["status"] == "modified"
    assert diff["stats"]["additions"] == 10


def test_commit_diff_falls_back_to_rest():
    svc = GitHubDataService()
    cache = MagicMock()
    cache.ensure_mirror.side_effect = RuntimeError("mirror unavailable")

    provider = MagicMock()
    provider.get_commit_diff.return_value = {"sha": "abc1234", "message": "x", "files": [], "stats": {}}

    with patch("agent.providers.git_mirror_provider.get_git_mirror_cache", return_value=cache):
        with patch("agent.providers.github_provider.get_github_provider", return_value=provider):
            out = svc.commit_diff(repo="acme/repo", sha="abc123")

    assert out["source"] == "rest"
    assert out["diff"]["sha"] == "abc1234"


def test_recent_commits_api_first_passthrough():
    svc = GitHubDataService()
    provider = MagicMock()
    provider.get_recent_commits.return_value = [{"sha": "abc1234", "message": "m"}]

    with patch("agent.providers.github_provider.get_github_provider", return_value=provider):
        out = svc.recent_commits(repo="acme/repo", since=MagicMock(), until=MagicMock(), branch="main")

    assert out["source"] == "api"
    assert len(out["commits"]) == 1


def test_workflow_runs_defaults_to_failed_jobs_mode():
    svc = GitHubDataService()
    provider = MagicMock()
    provider.get_workflow_runs.return_value = []
    since = MagicMock()

    with patch("agent.providers.github_provider.get_github_provider", return_value=provider):
        out = svc.workflow_runs(repo="acme/repo", since=since, limit=5)

    assert out["source"] == "api"
    provider.get_workflow_runs.assert_called_once_with(repo="acme/repo", since=since, limit=5, jobs_mode="failed")
