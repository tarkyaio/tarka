"""
Unit tests for GitHub provider with mocked API.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from agent.providers.github_provider import DefaultGitHubProvider


@pytest.fixture
def mock_github_env(monkeypatch):
    """Mock GitHub App environment variables."""
    # Use a test RSA private key (generated for testing only)
    test_private_key = """-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA0Z3VS5JJcds3xfn/ygWyF84dLAGJnP3fh4JqLSPLKfHPxe7L
QHnmN0MYZXKD+m0JILmR1Y5FQh0hCXJC8z3F5dqnPJL1jHEoHhALPXN4l/I3ZYYr
YTXVZ2t0rKfMz1qQJ8K3VqWsJHEZ0nLI0NxXxDsQwXn0K7P+GRbFsE0dQOWJ0B0l
ZM0xR5/Ds0yBpTMfR8VkOQD5qKPxwXZ5uI1cJ3p4KFMxA0TxZxBwZxVqJfzXgOQG
ScWLPLqXY0F7K7lEH8ZlKLqQ4JL9YTTfYnFYcP0D8lHHJ8QXV0OXQJxTqRzBp7cZ
QHN0Lqv3PxKEH1F1qJ8CZPqYHXUvXQmLQJ9IcwIDAQABAoIBADGM0YPMb8lqHhBV
3l7PbTJ5F4xsWxJ0Fn1MqR1SQfLqWXqxG7BFHr1mxNQXF8rXhDXqJ0p3qMvR8fFp
nQKPx7J8xYQN8pDQl7MQxHqJ0yBJ2YJ1Q8vqHXqJ0vF9xQ0F8J5nQJ0F8pDJ2F9Q
pDQJ8F7xQ9F8J5nQJ0F8pDJ2F9QpDQJ8F7xQ9F8J5nQJ0F8pDJ2F9QpDQJ8F7xQ9
F8J5nQJ0F8pDJ2F9QpDQJ8F7xQ9F8J5nQJ0F8pDJ2F9QpDQJ8F7xQ9F8J5nQJ0F8
pDJ2F9QpDQJ8F7xQ9F8J5nQJ0F8pDJ2F9QpDQJ8F7xQ9F8J5nQJ0F8pDJ2F9QpDQ
J8F7xQ9ECgYEA7J9QpDQJ8F7xQ9F8J5nQJ0F8pDJ2F9QpDQJ8F7xQ9F8J5nQJ0F8
pDJ2F9QpDQJ8F7xQ9F8J5nQJ0F8pDJ2F9QpDQJ8F7xQ9F8J5nQJ0F8pDJ2F9QpDQ
J8F7xQ9F8J5nQJ0F8pDJ2F9QpDQJ8F7xQ9ECgYEA4J5nQJ0F8pDJ2F9QpDQJ8F7x
Q9F8J5nQJ0F8pDJ2F9QpDQJ8F7xQ9F8J5nQJ0F8pDJ2F9QpDQJ8F7xQ9F8J5nQJ0
F8pDJ2F9QpDQJ8F7xQ9F8J5nQJ0F8pDJ2F9QpDQJ8F7xQ9ECgYBQJ0F8pDJ2F9Qp
DQJ8F7xQ9F8J5nQJ0F8pDJ2F9QpDQJ8F7xQ9F8J5nQJ0F8pDJ2F9QpDQJ8F7xQ9F
8J5nQJ0F8pDJ2F9QpDQJ8F7xQ9F8J5nQJ0F8pDJ2F9QpDQJ8F7xQ9ECgYBJ0F8pD
J2F9QpDQJ8F7xQ9F8J5nQJ0F8pDJ2F9QpDQJ8F7xQ9F8J5nQJ0F8pDJ2F9QpDQJ8
F7xQ9F8J5nQJ0F8pDJ2F9QpDQJ8F7xQ9F8J5nQJ0F8pDJ2F9QpDQJ8F7xQ9ECgYA
J0F8pDJ2F9QpDQJ8F7xQ9F8J5nQJ0F8pDJ2F9QpDQJ8F7xQ9F8J5nQJ0F8pDJ2F9
QpDQJ8F7xQ9F8J5nQJ0F8pDJ2F9QpDQJ8F7xQ9F8J5nQJ0F8pDJ2F9QpDQ==
-----END RSA PRIVATE KEY-----"""

    monkeypatch.setenv("GITHUB_APP_ID", "123456")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", test_private_key)
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "78901")


def test_github_provider_init_loads_env_vars(mock_github_env):
    """Provider loads credentials from environment variables."""
    provider = DefaultGitHubProvider()

    assert provider.app_id == "123456"
    assert "BEGIN RSA PRIVATE KEY" in provider.private_key
    assert provider.installation_id == "78901"


def test_github_provider_generates_jwt(mock_github_env):
    """Provider generates valid JWT tokens."""
    provider = DefaultGitHubProvider()

    # Mock JWT encoding to avoid needing a real key
    with patch("jwt.encode", return_value="mocked.jwt.token"):
        jwt_token = provider._generate_jwt()

        assert jwt_token == "mocked.jwt.token"
        assert isinstance(jwt_token, str)


def test_github_provider_jwt_missing_credentials(monkeypatch):
    """Provider raises error if credentials are missing."""
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY", raising=False)

    provider = DefaultGitHubProvider()

    with pytest.raises(ValueError, match="GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY required"):
        provider._generate_jwt()


def test_github_provider_gets_installation_token(mock_github_env):
    """Provider retrieves installation token from GitHub API."""
    provider = DefaultGitHubProvider()

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "token": "ghs_test_token_abc123",
        "expires_at": "2026-02-18T13:00:00Z",
    }
    mock_response.raise_for_status = MagicMock()

    with patch("jwt.encode", return_value="mocked.jwt.token"):
        with patch("requests.post", return_value=mock_response) as mock_post:
            token = provider._get_installation_token()

            assert token == "ghs_test_token_abc123"
            assert provider._installation_token == "ghs_test_token_abc123"
            assert provider._token_expires_at is not None

            # Verify API call
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert "https://api.github.com/app/installations/78901/access_tokens" in call_args[0]
            assert "Bearer " in call_args[1]["headers"]["Authorization"]


def test_github_provider_caches_installation_token(mock_github_env):
    """Provider caches installation token and reuses it."""
    provider = DefaultGitHubProvider()

    # Set up cached token
    provider._installation_token = "ghs_cached_token"
    provider._token_expires_at = datetime.now(timezone.utc).replace(hour=23, minute=59)

    # Should return cached token without API call
    with patch("requests.post") as mock_post:
        token = provider._get_installation_token()

        assert token == "ghs_cached_token"
        mock_post.assert_not_called()


def test_github_provider_refreshes_expired_token(mock_github_env):
    """Provider refreshes token when it's close to expiry."""
    provider = DefaultGitHubProvider()

    # Set up expired token (in the past)
    provider._installation_token = "ghs_old_token"
    provider._token_expires_at = datetime.now(timezone.utc).replace(hour=0, minute=0)

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "token": "ghs_new_token_xyz789",
        "expires_at": "2026-02-18T14:00:00Z",
    }
    mock_response.raise_for_status = MagicMock()

    with patch("jwt.encode", return_value="mocked.jwt.token"):
        with patch("requests.post", return_value=mock_response):
            token = provider._get_installation_token()

            # Should have new token
            assert token == "ghs_new_token_xyz789"
            assert provider._installation_token == "ghs_new_token_xyz789"


def test_github_provider_make_request_uses_auth(mock_github_env):
    """Provider adds authentication headers to requests."""
    provider = DefaultGitHubProvider()
    provider._installation_token = "ghs_test_token"
    provider._token_expires_at = datetime.now(timezone.utc).replace(hour=23, minute=59)

    mock_response = MagicMock()
    mock_response.json.return_value = {"name": "test-repo"}
    mock_response.raise_for_status = MagicMock()

    with patch("requests.request", return_value=mock_response) as mock_request:
        response = provider._make_request("GET", "https://api.github.com/repos/myorg/myrepo")

        assert response.json()["name"] == "test-repo"

        # Verify auth headers
        call_args = mock_request.call_args
        headers = call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer ghs_test_token"
        assert headers["Accept"] == "application/vnd.github+json"
        assert headers["X-GitHub-Api-Version"] == "2022-11-28"


def test_github_provider_missing_installation_id(mock_github_env, monkeypatch):
    """Provider raises error if installation ID is missing."""
    monkeypatch.delenv("GITHUB_APP_INSTALLATION_ID", raising=False)

    provider = DefaultGitHubProvider()

    with patch("jwt.encode", return_value="mocked.jwt.token"):
        with pytest.raises(ValueError, match="GITHUB_APP_INSTALLATION_ID required"):
            provider._get_installation_token()


def test_github_provider_get_recent_commits(mock_github_env):
    """Provider retrieves recent commits."""
    provider = DefaultGitHubProvider()
    provider._installation_token = "ghs_test_token"
    provider._token_expires_at = datetime.now(timezone.utc).replace(hour=23, minute=59)

    mock_response = MagicMock()
    mock_response.json.return_value = [
        {
            "sha": "abc123def456",
            "commit": {
                "author": {"name": "Alice", "date": "2026-02-18T10:00:00Z"},
                "message": "fix: resolve memory leak\n\nDetailed description...",
            },
            "html_url": "https://github.com/myorg/myrepo/commit/abc123",
        },
        {
            "sha": "def456ghi789",
            "commit": {
                "author": {"name": "Bob", "date": "2026-02-18T09:00:00Z"},
                "message": "feat: add retry logic",
            },
            "html_url": "https://github.com/myorg/myrepo/commit/def456",
        },
    ]
    mock_response.raise_for_status = MagicMock()

    with patch("requests.request", return_value=mock_response) as mock_request:
        since = datetime(2026, 2, 18, 8, 0, 0, tzinfo=timezone.utc)
        until = datetime(2026, 2, 18, 12, 0, 0, tzinfo=timezone.utc)

        commits = provider.get_recent_commits("myorg/myrepo", since, until, branch="main")

        assert len(commits) == 2
        assert commits[0]["sha"] == "abc123d"  # Short SHA
        assert commits[0]["author"] == "Alice"
        assert (
            commits[0]["message"] == "fix: resolve memory leak\n\nDetailed description..."
        )  # Full message (capped at 300 chars)
        assert commits[1]["sha"] == "def456g"
        assert commits[1]["author"] == "Bob"

        # Verify API call
        call_args = mock_request.call_args
        assert "https://api.github.com/repos/myorg/myrepo/commits" in call_args[0]
        params = call_args[1]["params"]
        assert params["sha"] == "main"
        assert params["since"] == since.isoformat()
        assert params["until"] == until.isoformat()


def test_github_provider_get_commits_handles_errors(mock_github_env):
    """Provider handles commit query errors gracefully."""
    provider = DefaultGitHubProvider()
    provider._installation_token = "ghs_test_token"
    provider._token_expires_at = datetime.now(timezone.utc).replace(hour=23, minute=59)

    with patch("requests.request", side_effect=Exception("API error")):
        since = datetime(2026, 2, 18, 8, 0, 0, tzinfo=timezone.utc)
        until = datetime(2026, 2, 18, 12, 0, 0, tzinfo=timezone.utc)

        commits = provider.get_recent_commits("myorg/myrepo", since, until)

        # Should return error dict instead of raising
        assert len(commits) == 1
        assert "error" in commits[0]
        assert "github_error" in commits[0]["error"]


def test_github_provider_get_workflow_runs(mock_github_env):
    """Provider retrieves workflow runs."""
    provider = DefaultGitHubProvider()
    provider._installation_token = "ghs_test_token"
    provider._token_expires_at = datetime.now(timezone.utc).replace(hour=23, minute=59)

    # Mock workflow runs API response
    mock_runs_response = MagicMock()
    mock_runs_response.json.return_value = {
        "workflow_runs": [
            {
                "id": 12345,
                "name": "CI",
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-02-18T10:00:00Z",
                "updated_at": "2026-02-18T10:05:00Z",
                "html_url": "https://github.com/myorg/myrepo/actions/runs/12345",
            },
            {
                "id": 12346,
                "name": "Deploy",
                "status": "completed",
                "conclusion": "failure",
                "created_at": "2026-02-18T09:00:00Z",
                "updated_at": "2026-02-18T09:10:00Z",
                "html_url": "https://github.com/myorg/myrepo/actions/runs/12346",
            },
        ]
    }

    # Mock jobs API responses
    mock_jobs_response_1 = MagicMock()
    mock_jobs_response_1.json.return_value = {
        "jobs": [
            {"id": 101, "name": "build", "status": "completed", "conclusion": "success"},
            {"id": 102, "name": "test", "status": "completed", "conclusion": "success"},
        ]
    }

    mock_jobs_response_2 = MagicMock()
    mock_jobs_response_2.json.return_value = {
        "jobs": [
            {"id": 201, "name": "deploy", "status": "completed", "conclusion": "failure"},
        ]
    }

    # Mock _make_request to return different responses based on URL
    def mock_make_request(method, url, **kwargs):
        if "/actions/runs" in url and "/jobs" not in url:
            return mock_runs_response
        elif "/jobs" in url and "12345" in url:
            return mock_jobs_response_1
        elif "/jobs" in url and "12346" in url:
            return mock_jobs_response_2
        return MagicMock(json=lambda: {})

    with patch.object(provider, "_make_request", side_effect=mock_make_request):
        since = datetime(2026, 2, 18, 8, 0, 0, tzinfo=timezone.utc)
        runs = provider.get_workflow_runs("myorg/myrepo", since, limit=10)

        assert len(runs) == 2
        assert runs[0]["id"] == 12345
        assert runs[0]["workflow_name"] == "CI"
        assert runs[0]["conclusion"] == "success"
        assert len(runs[0]["jobs"]) == 2
        assert runs[1]["id"] == 12346
        assert runs[1]["conclusion"] == "failure"
        assert len(runs[1]["jobs"]) == 1


def test_github_provider_get_workflow_logs(mock_github_env):
    """Provider retrieves workflow job logs."""
    provider = DefaultGitHubProvider()
    provider._installation_token = "ghs_test_token"
    provider._token_expires_at = datetime.now(timezone.utc).replace(hour=23, minute=59)

    mock_response = MagicMock()
    mock_response.text = "Line 1\nLine 2\nLine 3\nBuild failed: connection timeout"
    mock_response.raise_for_status = MagicMock()

    with patch("requests.request", return_value=mock_response):
        logs = provider.get_workflow_run_logs("myorg/myrepo", run_id=12345, job_id=101)

        assert "Line 1" in logs
        assert "Build failed" in logs


def test_github_provider_get_workflow_logs_truncates_large_output(mock_github_env):
    """Provider truncates large log output."""
    provider = DefaultGitHubProvider()
    provider._installation_token = "ghs_test_token"
    provider._token_expires_at = datetime.now(timezone.utc).replace(hour=23, minute=59)

    # Create logs with 100 lines
    large_logs = "\n".join([f"Line {i}" for i in range(100)])

    mock_response = MagicMock()
    mock_response.text = large_logs
    mock_response.raise_for_status = MagicMock()

    with patch("requests.request", return_value=mock_response):
        logs = provider.get_workflow_run_logs("myorg/myrepo", run_id=12345, job_id=101)

        # Should contain first and last lines but be truncated
        assert "Line 0" in logs
        assert "Line 99" in logs
        assert "truncated" in logs


def test_github_provider_get_workflow_runs_handles_errors(mock_github_env):
    """Provider handles workflow query errors gracefully."""
    provider = DefaultGitHubProvider()
    provider._installation_token = "ghs_test_token"
    provider._token_expires_at = datetime.now(timezone.utc).replace(hour=23, minute=59)

    with patch("requests.request", side_effect=Exception("API error")):
        since = datetime(2026, 2, 18, 8, 0, 0, tzinfo=timezone.utc)
        runs = provider.get_workflow_runs("myorg/myrepo", since)

        # Should return error dict instead of raising
        assert len(runs) == 1
        assert "error" in runs[0]


def test_github_provider_get_workflow_logs_handles_errors(mock_github_env):
    """Provider handles log fetch errors gracefully."""
    provider = DefaultGitHubProvider()
    provider._installation_token = "ghs_test_token"
    provider._token_expires_at = datetime.now(timezone.utc).replace(hour=23, minute=59)

    with patch("requests.request", side_effect=Exception("API error")):
        logs = provider.get_workflow_run_logs("myorg/myrepo", run_id=12345, job_id=101)

        # Should return error message instead of raising
        assert "Error fetching logs" in logs
        assert "API error" in logs


def test_github_provider_get_file_contents(mock_github_env):
    """Provider retrieves file contents."""
    import base64

    provider = DefaultGitHubProvider()
    provider._installation_token = "ghs_test_token"
    provider._token_expires_at = datetime.now(timezone.utc).replace(hour=23, minute=59)

    # Mock file contents response (base64-encoded)
    file_content = "# My README\n\nThis is a test repository."
    encoded_content = base64.b64encode(file_content.encode("utf-8")).decode("utf-8")

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "name": "README.md",
        "path": "README.md",
        "content": encoded_content,
        "encoding": "base64",
    }

    with patch.object(provider, "_make_request", return_value=mock_response):
        contents = provider.get_file_contents("myorg/myrepo", "README.md", ref="main")

        assert contents == file_content
        assert "My README" in contents


def test_github_provider_list_directory(mock_github_env):
    """Provider lists files in directory."""
    provider = DefaultGitHubProvider()
    provider._installation_token = "ghs_test_token"
    provider._token_expires_at = datetime.now(timezone.utc).replace(hour=23, minute=59)

    mock_response = MagicMock()
    mock_response.json.return_value = [
        {"name": "setup.md", "type": "file"},
        {"name": "architecture.md", "type": "file"},
        {"name": "images", "type": "dir"},  # Should be filtered out
        {"name": "troubleshooting.md", "type": "file"},
    ]

    with patch.object(provider, "_make_request", return_value=mock_response):
        files = provider.list_directory("myorg/myrepo", "docs", ref="main")

        assert len(files) == 3
        assert "setup.md" in files
        assert "architecture.md" in files
        assert "troubleshooting.md" in files
        assert "images" not in files  # Directory excluded


def test_github_provider_get_repository_metadata(mock_github_env):
    """Provider retrieves repository metadata."""
    provider = DefaultGitHubProvider()
    provider._installation_token = "ghs_test_token"
    provider._token_expires_at = datetime.now(timezone.utc).replace(hour=23, minute=59)

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "name": "myrepo",
        "full_name": "myorg/myrepo",
        "description": "A test repository",
        "default_branch": "main",
        "html_url": "https://github.com/myorg/myrepo",
    }

    with patch.object(provider, "_make_request", return_value=mock_response):
        metadata = provider.get_repository_metadata("myorg/myrepo")

        assert metadata["name"] == "myrepo"
        assert metadata["full_name"] == "myorg/myrepo"
        assert metadata["description"] == "A test repository"
        assert metadata["default_branch"] == "main"
        assert metadata["html_url"] == "https://github.com/myorg/myrepo"


def test_github_provider_get_repository_metadata_handles_errors(mock_github_env):
    """Provider handles repository metadata errors gracefully."""
    provider = DefaultGitHubProvider()
    provider._installation_token = "ghs_test_token"
    provider._token_expires_at = datetime.now(timezone.utc).replace(hour=23, minute=59)

    with patch.object(provider, "_make_request", side_effect=Exception("API error")):
        metadata = provider.get_repository_metadata("myorg/myrepo")

        # Should return error dict instead of raising
        assert "error" in metadata
        assert "github_error" in metadata["error"]
