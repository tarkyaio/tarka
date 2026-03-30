"""
Shared GitHub data service used by both chat tools and pipeline collectors.

This centralizes:
- metadata API access (commits/workflows/logs)
- local mirror usage for blob/content reads
- mirror->REST fallback behavior
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.authz.policy import redact_text

logger = logging.getLogger(__name__)

_STATUS_MAP = {
    "M": "modified",
    "A": "added",
    "D": "removed",
    "R": "renamed",
    "C": "copied",
    "T": "changed",
    "U": "unmerged",
}


def _safe_error_message(err: Exception) -> str:
    return redact_text(str(err), redact_infrastructure=False)[:300]


class GitHubDataService:
    """Read-only service for GitHub metadata + content access."""

    def recent_commits(
        self,
        *,
        repo: str,
        since: datetime,
        until: datetime,
        branch: str = "main",
    ) -> Dict[str, Any]:
        """API-first metadata path (selected policy)."""
        try:
            from agent.providers.github_provider import get_github_provider

            github = get_github_provider()
            commits = github.get_recent_commits(repo=repo, since=since, until=until, branch=branch)
            if commits and isinstance(commits, list) and isinstance(commits[0], dict) and "error" in commits[0]:
                return {
                    "source": "api",
                    "error": commits[0].get("error", "github_error"),
                    "message": commits[0].get("message", ""),
                    "commits": [],
                }
            return {"source": "api", "commits": commits}
        except Exception as e:
            return {"source": "api", "error": f"github_error:{type(e).__name__}", "message": _safe_error_message(e)}

    def workflow_runs(
        self,
        *,
        repo: str,
        since: datetime,
        limit: int = 10,
        jobs_mode: str = "failed",
    ) -> Dict[str, Any]:
        try:
            from agent.providers.github_provider import get_github_provider

            github = get_github_provider()
            runs = github.get_workflow_runs(repo=repo, since=since, limit=limit, jobs_mode=jobs_mode)
            if runs and isinstance(runs, list) and isinstance(runs[0], dict) and "error" in runs[0]:
                return {
                    "source": "api",
                    "error": runs[0].get("error", "github_error"),
                    "message": runs[0].get("message", ""),
                    "workflow_runs": [],
                }
            return {"source": "api", "workflow_runs": runs}
        except Exception as e:
            return {"source": "api", "error": f"github_error:{type(e).__name__}", "message": _safe_error_message(e)}

    def workflow_logs(self, *, repo: str, run_id: int, job_id: int) -> Dict[str, Any]:
        try:
            from agent.providers.github_provider import get_github_provider

            github = get_github_provider()
            logs = github.get_workflow_run_logs(repo=repo, run_id=run_id, job_id=job_id)
            if str(logs).startswith("Error fetching logs"):
                return {"source": "api", "error": "github_error:log_fetch_failed", "message": logs}
            return {"source": "api", "logs": logs}
        except Exception as e:
            return {"source": "api", "error": f"github_error:{type(e).__name__}", "message": _safe_error_message(e)}

    def read_file(self, *, repo: str, path: str, ref: str = "main") -> Dict[str, Any]:
        """Mirror-first for blob reads, REST fallback."""
        mirror_error: Optional[str] = None
        try:
            from agent.providers.git_mirror_provider import get_git_mirror_cache

            git_cache = get_git_mirror_cache()
            mirror_path = git_cache.ensure_mirror(repo=repo)
            content = git_cache.read_file(mirror_path, ref=ref, file_path=path)
            return {"source": "mirror", "content": content}
        except Exception as e:
            mirror_error = _safe_error_message(e)

        try:
            from agent.providers.github_provider import get_github_provider

            github = get_github_provider()
            content = github.get_file_contents(repo=repo, path=path, ref=ref)
            return {
                "source": "rest",
                "content": content,
                "fallback_from": "mirror",
                "mirror_error": mirror_error,
            }
        except Exception as e:
            return {
                "source": "none",
                "error": f"github_error:{type(e).__name__}",
                "message": _safe_error_message(e),
                "mirror_error": mirror_error,
            }

    def commit_diff(self, *, repo: str, sha: str) -> Dict[str, Any]:
        """Mirror-first for commit diff blobs, REST fallback."""
        mirror_error: Optional[str] = None
        try:
            from agent.providers.git_mirror_provider import get_git_mirror_cache

            git_cache = get_git_mirror_cache()
            mirror_path = git_cache.ensure_mirror(repo=repo)
            diff = self._commit_diff_from_mirror(git_cache, mirror_path, sha=sha)
            return {"source": "mirror", "diff": diff}
        except Exception as e:
            mirror_error = _safe_error_message(e)

        try:
            from agent.providers.github_provider import get_github_provider

            github = get_github_provider()
            diff = github.get_commit_diff(repo=repo, sha=sha)
            if isinstance(diff, dict) and "error" in diff:
                return {
                    "source": "rest",
                    "error": diff.get("error", "github_error"),
                    "message": diff.get("message", ""),
                    "mirror_error": mirror_error,
                }
            return {"source": "rest", "diff": diff, "fallback_from": "mirror", "mirror_error": mirror_error}
        except Exception as e:
            return {
                "source": "none",
                "error": f"github_error:{type(e).__name__}",
                "message": _safe_error_message(e),
                "mirror_error": mirror_error,
            }

    def readme_and_docs(
        self,
        *,
        repo: str,
        mirror_ref: str = "HEAD",
        api_ref: str = "main",
        max_docs: int = 5,
    ) -> Dict[str, Any]:
        """Collector helper: mirror-first README/docs with REST fallback."""
        readme: Optional[str] = None
        docs: List[Dict[str, str]] = []
        source_parts: List[str] = []
        mirror_error: Optional[str] = None
        mirror_readme_failed = False
        mirror_docs_failed = False

        try:
            from agent.providers.git_mirror_provider import get_git_mirror_cache

            git_cache = get_git_mirror_cache()
            mirror_path = git_cache.ensure_mirror(repo=repo)
            source_parts.append("mirror")

            try:
                readme = git_cache.read_file(mirror_path, ref=mirror_ref, file_path="README.md")
            except Exception:
                mirror_readme_failed = True

            try:
                doc_files = git_cache.list_dir(mirror_path, ref=mirror_ref, dir_path="docs")
                for file in doc_files[:max_docs]:
                    if file.endswith(".md"):
                        try:
                            content = git_cache.read_file(mirror_path, ref=mirror_ref, file_path=f"docs/{file}")
                            docs.append({"path": f"docs/{file}", "content": content})
                        except Exception:
                            continue
            except Exception:
                mirror_docs_failed = True
        except Exception as e:
            mirror_error = _safe_error_message(e)
            mirror_readme_failed = True
            mirror_docs_failed = True

        try:
            from agent.providers.github_provider import get_github_provider

            github = get_github_provider()
            if readme is None and mirror_readme_failed:
                try:
                    readme = github.get_file_contents(repo=repo, path="README.md", ref=api_ref)
                    source_parts.append("rest")
                except Exception:
                    pass

            if not docs and mirror_docs_failed:
                try:
                    doc_files = github.list_directory(repo=repo, path="docs", ref=api_ref)
                    for file in doc_files[:max_docs]:
                        if file.endswith(".md"):
                            try:
                                content = github.get_file_contents(repo=repo, path=f"docs/{file}", ref=api_ref)
                                docs.append({"path": f"docs/{file}", "content": content})
                            except Exception:
                                continue
                    if docs:
                        source_parts.append("rest")
                except Exception:
                    pass
        except Exception:
            pass

        source = "none"
        if source_parts:
            unique = sorted(set(source_parts))
            source = unique[0] if len(unique) == 1 else "mixed"

        return {
            "source": source,
            "readme": readme,
            "docs": docs,
            "mirror_error": mirror_error,
        }

    def _commit_diff_from_mirror(self, git_cache: Any, mirror_path: Path, *, sha: str) -> Dict[str, Any]:
        short_sha = git_cache.run_git(mirror_path, ["rev-parse", "--short=7", sha]).strip()[:7]
        message = git_cache.run_git(mirror_path, ["show", "-s", "--format=%B", sha]).strip()[:300]

        name_status_out = git_cache.run_git(mirror_path, ["show", "--name-status", "--format=", sha])
        numstat_out = git_cache.run_git(mirror_path, ["show", "--numstat", "--format=", sha])
        patch_out = git_cache.run_git(mirror_path, ["show", "--format=", "--patch", sha])

        status_by_path: Dict[str, str] = {}
        for line in name_status_out.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            code = parts[0]
            path = parts[-1]
            status_by_path[path] = _STATUS_MAP.get(code[:1], code)

        adds_dels: Dict[str, Tuple[int, int]] = {}
        for line in numstat_out.splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            add_raw, del_raw, path = parts[0], parts[1], parts[2]
            try:
                add = int(add_raw)
            except Exception:
                add = 0
            try:
                dele = int(del_raw)
            except Exception:
                dele = 0
            adds_dels[path] = (add, dele)

        patch_by_path = self._split_patch_by_file(patch_out)
        all_paths = sorted(set(status_by_path.keys()) | set(adds_dels.keys()) | set(patch_by_path.keys()))

        files: List[Dict[str, Any]] = []
        total_add = 0
        total_del = 0
        for path in all_paths:
            add, dele = adds_dels.get(path, (0, 0))
            total_add += add
            total_del += dele
            entry: Dict[str, Any] = {
                "filename": path,
                "status": status_by_path.get(path, "modified"),
                "additions": add,
                "deletions": dele,
            }
            patch = "\n".join(patch_by_path.get(path, []))
            if patch:
                entry["patch"] = patch[:500] + ("..." if len(patch) > 500 else "")
            files.append(entry)

        return {
            "sha": short_sha,
            "message": message,
            "files": files[:20],
            "stats": {"total": len(files), "additions": total_add, "deletions": total_del},
        }

    def _split_patch_by_file(self, patch_text: str) -> Dict[str, List[str]]:
        chunks: Dict[str, List[str]] = {}
        current_path: Optional[str] = None
        current_lines: List[str] = []
        header_re = re.compile(r"^diff --git a/(.+?) b/(.+)$")

        for line in (patch_text or "").splitlines():
            m = header_re.match(line)
            if m:
                if current_path is not None:
                    chunks[current_path] = current_lines
                current_path = m.group(2)
                current_lines = [line]
                continue
            if current_path is not None:
                current_lines.append(line)

        if current_path is not None:
            chunks[current_path] = current_lines
        return chunks


_github_data_service: Optional[GitHubDataService] = None


def get_github_data_service() -> GitHubDataService:
    global _github_data_service
    if _github_data_service is None:
        _github_data_service = GitHubDataService()
    return _github_data_service


def set_github_data_service(service: GitHubDataService) -> None:
    global _github_data_service
    _github_data_service = service
