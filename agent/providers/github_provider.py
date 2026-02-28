"""
GitHub provider for retrieving code changes, workflows, and documentation.

Supports GitHub App authentication with installation tokens.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Protocol

import jwt
import requests


class GitHubProvider(Protocol):
    """Protocol for GitHub API access (read-only)."""

    def get_recent_commits(
        self,
        repo: str,
        since: datetime,
        until: datetime,
        branch: str = "main",
    ) -> List[Dict[str, Any]]:
        """
        Get recent commits in a repository.

        Args:
            repo: Repository in "org/repo" format
            since: Start of time window
            until: End of time window
            branch: Branch name (default: main)

        Returns:
            List of commit dicts with keys:
            - sha: Commit SHA
            - author: Author name
            - message: Commit message
            - timestamp: ISO timestamp
            - url: GitHub URL
        """
        ...

    def get_workflow_runs(
        self,
        repo: str,
        since: datetime,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Get recent GitHub Actions workflow runs.

        Args:
            repo: Repository in "org/repo" format
            since: Start of time window
            limit: Maximum number of runs to return

        Returns:
            List of workflow run dicts with keys:
            - id: Run ID
            - workflow_name: Workflow name
            - status: queued, in_progress, completed
            - conclusion: success, failure, cancelled, etc.
            - created_at: ISO timestamp
            - updated_at: ISO timestamp
            - url: GitHub URL
            - jobs: List of job dicts (id, name, status, conclusion)
        """
        ...

    def get_workflow_run_logs(
        self,
        repo: str,
        run_id: int,
        job_id: int,
    ) -> str:
        """
        Get logs for a specific workflow run job.

        Args:
            repo: Repository in "org/repo" format
            run_id: Workflow run ID
            job_id: Job ID

        Returns:
            Job logs as string (may be truncated)
        """
        ...

    def get_commit_diff(
        self,
        repo: str,
        sha: str,
    ) -> Dict[str, Any]:
        """
        Get diff/changed files for a single commit.

        Args:
            repo: Repository in "org/repo" format
            sha: Commit SHA (short or full)

        Returns:
            Dict with keys:
            - sha: Short commit SHA
            - message: Full commit message (capped at 300 chars)
            - files: List of file dicts (filename, status, additions, deletions, patch)
            - stats: Dict with total, additions, deletions
        """
        ...

    def get_file_contents(
        self,
        repo: str,
        path: str,
        ref: str = "main",
    ) -> str:
        """
        Get file contents from repository.

        Args:
            repo: Repository in "org/repo" format
            path: File path (e.g., "README.md", "docs/setup.md")
            ref: Git ref (branch, tag, commit SHA)

        Returns:
            File contents as string

        Raises:
            Exception on 404 or other errors
        """
        ...

    def list_directory(
        self,
        repo: str,
        path: str,
        ref: str = "main",
    ) -> List[str]:
        """
        List files in a directory.

        Args:
            repo: Repository in "org/repo" format
            path: Directory path (e.g., "docs")
            ref: Git ref (branch, tag, commit SHA)

        Returns:
            List of file paths relative to directory

        Raises:
            Exception on 404 or other errors
        """
        ...

    def repo_exists(self, repo: str) -> bool:
        """
        Fast check whether a repository exists and is accessible.

        Args:
            repo: Repository in "org/repo" format

        Returns:
            True if the repo exists and the app has access, False otherwise.
        """
        ...

    def get_repository_metadata(
        self,
        repo: str,
    ) -> Dict[str, Any]:
        """
        Get repository metadata.

        Args:
            repo: Repository in "org/repo" format

        Returns:
            Dict with keys:
            - name: Repo name
            - full_name: org/repo
            - description: Description
            - default_branch: Default branch name
            - html_url: GitHub URL
        """
        ...


class DefaultGitHubProvider:
    """
    Default GitHub provider using GitHub App authentication.

    Authenticates via GitHub App with JWT + installation token flow.
    Tokens are cached and auto-refreshed before expiry.

    Environment variables:
    - GITHUB_APP_ID: GitHub App ID
    - GITHUB_APP_PRIVATE_KEY: GitHub App private key (PEM format)
    - GITHUB_APP_INSTALLATION_ID: GitHub App installation ID
    """

    def __init__(self) -> None:
        """Initialize provider (authentication configured via env vars)."""
        self.app_id = os.getenv("GITHUB_APP_ID", "")
        self.private_key = os.getenv("GITHUB_APP_PRIVATE_KEY", "").replace("\\n", "\n")
        self.installation_id = os.getenv("GITHUB_APP_INSTALLATION_ID", "") or os.getenv("GITHUB_INSTALLATION_ID", "")

        # Token cache
        self._installation_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None

    def _generate_jwt(self) -> str:
        """
        Generate JWT for GitHub App authentication.

        Returns:
            JWT token string

        Raises:
            ValueError if credentials are missing
        """
        if not self.app_id or not self.private_key:
            raise ValueError("GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY required")

        now = int(time.time())
        payload = {
            "iat": now - 60,  # Issued 60 seconds in the past to allow for clock drift
            "exp": now + (10 * 60),  # Expires in 10 minutes
            "iss": self.app_id,
        }

        return jwt.encode(payload, self.private_key, algorithm="RS256")

    def _get_installation_token(self) -> str:
        """
        Get or refresh installation token.

        Returns:
            Installation token string

        Raises:
            Exception if token retrieval fails
        """
        # Check if cached token is still valid (refresh 5 minutes before expiry)
        if self._installation_token and self._token_expires_at:
            if datetime.now(timezone.utc) < (self._token_expires_at - timedelta(minutes=5)):
                return self._installation_token

        # Generate new installation token
        if not self.installation_id:
            raise ValueError("GITHUB_APP_INSTALLATION_ID required")

        jwt_token = self._generate_jwt()
        url = f"https://api.github.com/app/installations/{self.installation_id}/access_tokens"

        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        response = requests.post(url, headers=headers, timeout=10)
        response.raise_for_status()

        data = response.json()
        self._installation_token = data["token"]
        # GitHub tokens expire in 1 hour
        self._token_expires_at = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))

        return self._installation_token

    def _make_request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> requests.Response:
        """
        Make authenticated request to GitHub API.

        Args:
            method: HTTP method (GET, POST, etc.)
            url: API URL
            **kwargs: Additional arguments for requests

        Returns:
            Response object

        Raises:
            Exception on API errors
        """
        token = self._get_installation_token()

        headers = kwargs.pop("headers", {})
        headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )

        kwargs["headers"] = headers
        kwargs.setdefault("timeout", 10)

        response = requests.request(method, url, **kwargs)
        response.raise_for_status()
        return response

    def get_recent_commits(
        self,
        repo: str,
        since: datetime,
        until: datetime,
        branch: str = "main",
    ) -> List[Dict[str, Any]]:
        """
        Get recent commits in a repository.

        Returns commits in reverse chronological order (newest first).
        """
        try:
            url = f"https://api.github.com/repos/{repo}/commits"
            params = {
                "sha": branch,
                "since": since.isoformat(),
                "until": until.isoformat(),
                "per_page": 100,  # Max per page
            }

            response = self._make_request("GET", url, params=params)
            commits_raw = response.json()

            # Transform to simplified format
            commits = []
            for c in commits_raw:
                commit_data = c.get("commit", {})
                author_data = commit_data.get("author", {})

                commits.append(
                    {
                        "sha": c.get("sha", "")[:7],  # Short SHA
                        "author": author_data.get("name", "Unknown"),
                        "message": commit_data.get("message", "")[:300],
                        "timestamp": author_data.get("date", ""),
                        "url": c.get("html_url", ""),
                    }
                )

            return commits

        except Exception as e:
            # Return error dict instead of raising
            return [{"error": f"github_error:{type(e).__name__}", "message": str(e)}]

    def get_commit_diff(self, repo: str, sha: str) -> Dict[str, Any]:
        """Get diff/changed files for a single commit."""
        try:
            url = f"https://api.github.com/repos/{repo}/commits/{sha}"
            response = self._make_request("GET", url)
            data = response.json()

            commit_data = data.get("commit", {})
            files = []
            for f in data.get("files", []):
                entry: Dict[str, Any] = {
                    "filename": f.get("filename", ""),
                    "status": f.get("status", ""),
                    "additions": f.get("additions", 0),
                    "deletions": f.get("deletions", 0),
                }
                # Include patch but cap at 500 chars per file to prevent prompt bloat
                patch = f.get("patch", "")
                if patch:
                    entry["patch"] = patch[:500] + ("..." if len(patch) > 500 else "")
                files.append(entry)

            return {
                "sha": data.get("sha", "")[:7],
                "message": commit_data.get("message", "")[:300],
                "files": files[:20],  # Cap at 20 files
                "stats": data.get("stats", {}),
            }

        except Exception as e:
            return {"error": f"github_error:{type(e).__name__}", "message": str(e)}

    def get_workflow_runs(
        self,
        repo: str,
        since: datetime,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Get recent GitHub Actions workflow runs.

        Returns workflow runs in reverse chronological order (newest first).
        """
        try:
            url = f"https://api.github.com/repos/{repo}/actions/runs"
            params = {
                "created": f">={since.isoformat()}",
                "per_page": min(limit, 100),
            }

            response = self._make_request("GET", url, params=params)
            runs_raw = response.json().get("workflow_runs", [])

            # Transform to simplified format
            runs = []
            for r in runs_raw[:limit]:
                # Get jobs for this run
                jobs_url = f"https://api.github.com/repos/{repo}/actions/runs/{r['id']}/jobs"
                jobs_response = self._make_request("GET", jobs_url)
                jobs_raw = jobs_response.json().get("jobs", [])

                jobs = [
                    {
                        "id": j.get("id"),
                        "name": j.get("name", ""),
                        "status": j.get("status", ""),
                        "conclusion": j.get("conclusion", ""),
                    }
                    for j in jobs_raw
                ]

                runs.append(
                    {
                        "id": r.get("id"),
                        "workflow_name": r.get("name", ""),
                        "status": r.get("status", ""),
                        "conclusion": r.get("conclusion", ""),
                        "created_at": r.get("created_at", ""),
                        "updated_at": r.get("updated_at", ""),
                        "url": r.get("html_url", ""),
                        "jobs": jobs,
                    }
                )

            return runs

        except Exception as e:
            # Return error dict instead of raising
            return [{"error": f"github_error:{type(e).__name__}", "message": str(e)}]

    def get_workflow_run_logs(
        self,
        repo: str,
        run_id: int,
        job_id: int,
    ) -> str:
        """
        Get logs for a specific workflow run job.

        Returns logs as plain text, truncated to first/last 20 lines if too large.
        """
        try:
            url = f"https://api.github.com/repos/{repo}/actions/jobs/{job_id}/logs"
            response = self._make_request("GET", url)

            logs = response.text

            # Truncate if too large (keep first/last 20 lines)
            lines = logs.split("\n")
            if len(lines) > 40:
                first_20 = "\n".join(lines[:20])
                last_20 = "\n".join(lines[-20:])
                logs = f"{first_20}\n... [truncated {len(lines) - 40} lines] ...\n{last_20}"

            return logs

        except Exception as e:
            # Return error message instead of raising
            return f"Error fetching logs: {type(e).__name__}: {str(e)}"

    def get_file_contents(
        self,
        repo: str,
        path: str,
        ref: str = "main",
    ) -> str:
        """
        Get file contents from repository.

        Raises:
            Exception on 404 or other errors
        """
        import base64

        url = f"https://api.github.com/repos/{repo}/contents/{path}"
        params = {"ref": ref}

        response = self._make_request("GET", url, params=params)
        data = response.json()

        # GitHub returns base64-encoded content
        if "content" in data:
            content_b64 = data["content"].replace("\n", "")
            return base64.b64decode(content_b64).decode("utf-8")
        else:
            raise Exception(f"No content in response for {path}")

    def list_directory(
        self,
        repo: str,
        path: str,
        ref: str = "main",
    ) -> List[str]:
        """
        List files in a directory.

        Returns file names (not full paths).

        Raises:
            Exception on 404 or other errors
        """
        url = f"https://api.github.com/repos/{repo}/contents/{path}"
        params = {"ref": ref}

        response = self._make_request("GET", url, params=params)
        items = response.json()

        # Filter for files only (not directories)
        return [item["name"] for item in items if item["type"] == "file"]

    def repo_exists(self, repo: str) -> bool:
        """Fast HEAD check — returns True if the repo is accessible."""
        try:
            token = self._get_installation_token()
            url = f"https://api.github.com/repos/{repo}"
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            resp = requests.head(url, headers=headers, timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def get_repository_metadata(
        self,
        repo: str,
    ) -> Dict[str, Any]:
        """
        Get repository metadata.

        Returns:
            Dict with name, description, default_branch, html_url
        """
        try:
            url = f"https://api.github.com/repos/{repo}"
            response = self._make_request("GET", url)
            data = response.json()

            return {
                "name": data.get("name", ""),
                "full_name": data.get("full_name", ""),
                "description": data.get("description", ""),
                "default_branch": data.get("default_branch", "main"),
                "html_url": data.get("html_url", ""),
            }

        except Exception as e:
            # Return error dict instead of raising
            return {"error": f"github_error:{type(e).__name__}", "message": str(e)}


# Singleton instance
_github_provider: Optional[GitHubProvider] = None


def get_github_provider() -> GitHubProvider:
    """
    Get GitHub provider instance (singleton).

    Returns provider configured from environment variables.
    """
    global _github_provider
    if _github_provider is None:
        _github_provider = DefaultGitHubProvider()
    return _github_provider


def set_github_provider(provider: GitHubProvider) -> None:
    """Set GitHub provider instance (for testing)."""
    global _github_provider
    _github_provider = provider
