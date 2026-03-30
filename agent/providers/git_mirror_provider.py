"""
Local Git mirror cache provider for lightweight code/content access.

Design goals:
- Keep GitHub API usage for metadata only (commits/workflows/PR links).
- Use local bare mirrors for file/diff/history access.
- Avoid token leakage in logs.
- Be safe under concurrent access via per-repo file locks.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from agent.authz.policy import redact_text

try:
    import fcntl  # POSIX file locking
except Exception:  # pragma: no cover - only hit on non-POSIX platforms
    fcntl = None

logger = logging.getLogger(__name__)

_TOKEN_PATTERNS = [
    re.compile(r"(?i)(token|password|secret)=([^&\s]+)"),
    re.compile(r"(?i)https://([^:@/\s]+):([^@/\s]+)@"),
]
_REPO_LOCKS: Dict[str, threading.Lock] = {}
_REPO_LOCKS_GUARD = threading.Lock()


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _sanitize_repo(repo: str) -> Tuple[str, str]:
    value = (repo or "").strip().strip("/")
    parts = value.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"Invalid repo format: {repo!r}. Expected 'org/repo'")
    return parts[0], parts[1]


def _redact_sensitive(value: str) -> str:
    txt = value or ""
    for pat in _TOKEN_PATTERNS:
        txt = pat.sub(lambda m: m.group(0).replace(m.group(2), "[REDACTED]"), txt)
    return redact_text(txt, redact_infrastructure=False)


class GitMirrorCache:
    """Manage per-repo bare mirrors and local git content access."""

    _META_FILE = ".tarka_mirror_meta.json"

    def __init__(
        self,
        *,
        cache_root: Optional[Path] = None,
        fetch_ttl_seconds: Optional[int] = None,
        remote_base: Optional[str] = None,
        max_repos: Optional[int] = None,
        git_timeout_seconds: int = 30,
    ) -> None:
        self.cache_root = Path(cache_root or (os.getenv("TARKA_GIT_CACHE_DIR") or "/tmp/tarka/git")).expanduser()
        self.fetch_ttl_seconds = max(10, fetch_ttl_seconds or _env_int("TARKA_GIT_FETCH_TTL_SECONDS", 300))
        self.remote_base = (
            (remote_base or os.getenv("TARKA_GIT_REMOTE_BASE") or "https://github.com").strip().rstrip("/")
        )
        self.max_repos = max_repos if max_repos is not None else max(0, _env_int("TARKA_GIT_CACHE_MAX_REPOS", 0))
        self.git_timeout_seconds = max(5, int(git_timeout_seconds))
        self.cache_root.mkdir(parents=True, exist_ok=True)

    def mirror_dir(self, repo: str) -> Path:
        """Deterministic mirror path: <cache_root>/<org>/<repo>.git."""
        org, name = _sanitize_repo(repo)
        return self.cache_root / org / f"{name}.git"

    def build_remote_url(self, repo: str) -> str:
        """Build remote URL from configured base, without embedding credentials."""
        org, name = _sanitize_repo(repo)
        return f"{self.remote_base}/{org}/{name}.git"

    def ensure_mirror(self, repo: str, remote_url: Optional[str] = None) -> Path:
        """
        Ensure a bare mirror exists and is fresh.

        - Missing -> clone --mirror
        - Present + stale -> fetch -p origin
        """
        mirror_path = self.mirror_dir(repo)
        remote = (remote_url or self.build_remote_url(repo)).strip()
        mirror_path.parent.mkdir(parents=True, exist_ok=True)

        with self._repo_lock(mirror_path):
            fetched = False
            try:
                if not mirror_path.exists():
                    self.run_git(
                        None,
                        ["clone", "--mirror", remote, str(mirror_path)],
                        with_auth=True,
                        remote_url=remote,
                    )
                    fetched = True
                else:
                    if not (mirror_path / "HEAD").exists():
                        raise RuntimeError(f"Mirror path exists but is not a bare git repo: {mirror_path}")

                    # Keep origin aligned with configured remote (for GHE changes, etc.)
                    self.run_git(mirror_path, ["remote", "set-url", "origin", remote])
                    if not self.is_fresh(mirror_path):
                        self.run_git(
                            mirror_path,
                            ["fetch", "-p", "origin"],
                            with_auth=True,
                            remote_url=remote,
                        )
                        fetched = True
            except Exception:
                # If clone failed midway, remove broken directory so next call can retry.
                if mirror_path.exists() and not (mirror_path / "HEAD").exists():
                    shutil.rmtree(mirror_path, ignore_errors=True)
                raise

            self._update_metadata(mirror_path, repo=repo, remote_url=remote, fetched=fetched)

        self._enforce_lru_limit(exempt=mirror_path)
        return mirror_path

    def is_fresh(self, path: Path) -> bool:
        """Return True when last fetch time is within TTL."""
        if not path.exists() or not (path / "HEAD").exists():
            return False
        meta = self._read_metadata(path)
        last_fetch = meta.get("last_fetch_time")
        if not isinstance(last_fetch, (int, float)):
            return False
        return (time.time() - float(last_fetch)) <= self.fetch_ttl_seconds

    def run_git(
        self,
        path: Optional[Path],
        args: Sequence[str],
        *,
        with_auth: bool = False,
        remote_url: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        allow_exit_codes: Optional[Sequence[int]] = None,
    ) -> str:
        """
        Safe git subprocess wrapper.

        - no shell
        - timeout
        - captures stderr
        - redacts secrets in raised errors/logging
        """
        cmd: List[str] = ["git"]
        if path is not None:
            cmd.extend(["-C", str(path)])
        cmd.extend(str(a) for a in args)

        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        if with_auth:
            env.update(self._git_auth_env(remote_url=remote_url))

        timeout = int(timeout_seconds or self.git_timeout_seconds)
        proc = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

        allowed = set(allow_exit_codes or [])
        if proc.returncode != 0 and proc.returncode not in allowed:
            stderr = _redact_sensitive(proc.stderr or proc.stdout or "")
            safe_cmd = " ".join(_redact_sensitive(part) for part in cmd)
            raise RuntimeError(f"git failed (exit={proc.returncode}) cmd={safe_cmd} err={stderr[:1200]}")

        return proc.stdout or ""

    def read_file(self, repo_path: Path, ref: str, file_path: str) -> str:
        self._mark_access(repo_path)
        normalized = file_path.lstrip("/")
        return self.run_git(repo_path, ["show", f"{ref}:{normalized}"])

    def list_dir(self, repo_path: Path, ref: str, dir_path: str) -> List[str]:
        self._mark_access(repo_path)
        normalized = dir_path.strip("/")
        target = ref if not normalized else f"{ref}:{normalized}"
        out = self.run_git(repo_path, ["ls-tree", "--name-only", target])
        return [line.strip() for line in out.splitlines() if line.strip()]

    def recent_commits(
        self,
        repo_path: Path,
        since: datetime,
        until: datetime,
        branch: str = "main",
    ) -> List[Dict[str, Any]]:
        self._mark_access(repo_path)
        fmt = "%H%x1f%an%x1f%ad%x1f%s"
        out = self.run_git(
            repo_path,
            [
                "log",
                branch,
                f"--since={since.isoformat()}",
                f"--until={until.isoformat()}",
                "--date=iso-strict",
                f"--pretty=format:{fmt}",
            ],
        )
        commits: List[Dict[str, Any]] = []
        for line in out.splitlines():
            parts = line.split("\x1f")
            if len(parts) != 4:
                continue
            sha, author, ts, msg = parts
            commits.append({"sha": sha[:7], "author": author, "timestamp": ts, "message": msg[:300]})
        return commits

    def diff_range(
        self,
        repo_path: Path,
        base: str,
        head: str,
        *,
        pathspec: Optional[Sequence[str]] = None,
    ) -> Tuple[List[Dict[str, str]], str]:
        self._mark_access(repo_path)
        tail: List[str] = []
        if pathspec:
            tail = ["--", *[str(p) for p in pathspec]]

        names = self.run_git(repo_path, ["diff", "--name-status", f"{base}..{head}", *tail])
        files: List[Dict[str, str]] = []
        for raw in names.splitlines():
            parts = raw.split("\t")
            if len(parts) < 2:
                continue
            status = parts[0]
            if status.startswith("R") and len(parts) >= 3:
                files.append({"status": status, "path": parts[2], "old_path": parts[1]})
            else:
                files.append({"status": status, "path": parts[1]})

        patch = self.run_git(repo_path, ["diff", f"{base}..{head}", *tail])
        return files, patch

    def grep(
        self,
        repo_path: Path,
        ref: str,
        pattern: str,
        *,
        pathspec: Optional[Sequence[str]] = None,
        ignore_case: bool = True,
        max_results: int = 100,
    ) -> List[Dict[str, Any]]:
        self._mark_access(repo_path)
        cmd: List[str] = ["grep", "-n", "--full-name", "-I"]
        if ignore_case:
            cmd.append("-i")
        cmd.extend(["-e", pattern, ref])
        if pathspec:
            cmd.extend(["--", *[str(p) for p in pathspec]])

        out = self.run_git(repo_path, cmd, allow_exit_codes=[1])  # exit=1 when no matches
        matches: List[Dict[str, Any]] = []
        for line in out.splitlines():
            # path:line:content
            first = line.find(":")
            second = line.find(":", first + 1) if first != -1 else -1
            if first == -1 or second == -1:
                continue
            path = line[:first]
            line_no_raw = line[first + 1 : second]
            snippet = line[second + 1 :]
            try:
                line_no = int(line_no_raw)
            except Exception:
                line_no = 0
            matches.append({"path": path, "line": line_no, "snippet": snippet[:300]})
            if len(matches) >= max_results:
                break
        return matches

    def blame(
        self,
        repo_path: Path,
        ref: str,
        path: str,
        *,
        line_range: Optional[Tuple[int, int]] = None,
    ) -> str:
        self._mark_access(repo_path)
        cmd = ["blame", ref]
        if line_range:
            start, end = line_range
            cmd.extend(["-L", f"{int(start)},{int(end)}"])
        cmd.extend(["--", path.lstrip("/")])
        return self.run_git(repo_path, cmd)

    def _git_auth_env(self, *, remote_url: Optional[str]) -> Dict[str, str]:
        token = self._resolve_git_auth_token()
        if not token:
            return {}

        # Use transient header config instead of embedding credentials in URL/config.
        # GitHub accepts both PAT and installation tokens with x-access-token.
        pair = f"x-access-token:{token}".encode("utf-8")
        basic = base64.b64encode(pair).decode("ascii")
        key = "http.extraheader"
        if remote_url:
            parsed = urlsplit(remote_url)
            if parsed.scheme in {"http", "https"} and parsed.netloc:
                key = f"http.{parsed.scheme}://{parsed.netloc}/.extraheader"
            elif parsed.scheme and parsed.scheme not in {"http", "https"}:
                return {}
        return {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": key,
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
        }

    def _resolve_git_auth_token(self) -> Optional[str]:
        # 1) Preferred explicit token source.
        env_token = (os.getenv("GITHUB_TOKEN") or "").strip()
        if env_token:
            return env_token

        # 2) Fallback to existing GitHub App provider installation token flow.
        try:
            from agent.providers.github_provider import get_github_provider

            provider = get_github_provider()
            getter = getattr(provider, "_get_installation_token", None)
            if callable(getter):
                token = str(getter() or "").strip()
                if token:
                    return token
        except Exception as e:
            logger.debug("Git mirror auth token fallback unavailable: %s", _redact_sensitive(str(e)))
        return None

    @contextmanager
    def _repo_lock(self, mirror_path: Path) -> Iterator[None]:
        lock_key = str(mirror_path)
        with _REPO_LOCKS_GUARD:
            inproc_lock = _REPO_LOCKS.get(lock_key)
            if inproc_lock is None:
                inproc_lock = threading.Lock()
                _REPO_LOCKS[lock_key] = inproc_lock

        inproc_lock.acquire()
        lock_path = Path(f"{mirror_path}.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(lock_path, "a+", encoding="utf-8") as lock_file:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if fcntl is not None:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            inproc_lock.release()

    def _metadata_path(self, mirror_path: Path) -> Path:
        return mirror_path / self._META_FILE

    def _read_metadata(self, mirror_path: Path) -> Dict[str, Any]:
        meta_path = self._metadata_path(mirror_path)
        if not meta_path.exists():
            return {}
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_metadata(self, mirror_path: Path, data: Dict[str, Any]) -> None:
        meta_path = self._metadata_path(mirror_path)
        try:
            meta_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        except Exception as e:  # pragma: no cover - best effort
            logger.debug("Failed writing git mirror metadata for %s: %s", mirror_path, _redact_sensitive(str(e)))

    def _mark_access(self, mirror_path: Path) -> None:
        if not mirror_path.exists():
            return
        meta = self._read_metadata(mirror_path)
        if not meta:
            return
        meta["last_access_time"] = time.time()
        self._write_metadata(mirror_path, meta)

    def _update_metadata(self, mirror_path: Path, *, repo: str, remote_url: str, fetched: bool) -> None:
        now = time.time()
        meta = self._read_metadata(mirror_path)
        if not isinstance(meta, dict):
            meta = {}
        meta["repo"] = repo
        meta["remote_url"] = remote_url
        meta["last_access_time"] = now
        if fetched or not isinstance(meta.get("last_fetch_time"), (int, float)):
            meta["last_fetch_time"] = now
        meta["size_bytes"] = self._estimate_size_bytes(mirror_path)
        self._write_metadata(mirror_path, meta)

    def _estimate_size_bytes(self, mirror_path: Path) -> int:
        total = 0
        try:
            for root, _dirs, files in os.walk(mirror_path):
                for name in files:
                    fp = Path(root) / name
                    try:
                        total += fp.stat().st_size
                    except Exception:
                        continue
        except Exception:
            return 0
        return total

    def _iter_mirrors(self) -> List[Path]:
        mirrors: List[Path] = []
        if not self.cache_root.exists():
            return mirrors
        for org_dir in self.cache_root.iterdir():
            if not org_dir.is_dir():
                continue
            for repo_dir in org_dir.iterdir():
                if repo_dir.is_dir() and repo_dir.name.endswith(".git") and (repo_dir / "HEAD").exists():
                    mirrors.append(repo_dir)
        return mirrors

    def _enforce_lru_limit(self, *, exempt: Optional[Path] = None) -> None:
        if self.max_repos <= 0:
            return

        mirrors = self._iter_mirrors()
        if len(mirrors) <= self.max_repos:
            return

        scored: List[Tuple[float, Path]] = []
        for path in mirrors:
            if exempt is not None and path == exempt:
                continue
            meta = self._read_metadata(path)
            ts = float(meta.get("last_access_time") or meta.get("last_fetch_time") or 0.0)
            scored.append((ts, path))

        scored.sort(key=lambda x: x[0])  # oldest access first

        to_remove = max(0, len(mirrors) - self.max_repos)
        removed = 0
        for _ts, victim in scored:
            if removed >= to_remove:
                break
            self._evict_mirror(victim)
            removed += 1

    def _evict_mirror(self, mirror_path: Path) -> None:
        try:
            with self._repo_lock(mirror_path):
                if mirror_path.exists():
                    shutil.rmtree(mirror_path, ignore_errors=True)
        except Exception as e:  # pragma: no cover - best effort
            logger.debug("Failed to evict mirror %s: %s", mirror_path, _redact_sensitive(str(e)))


_git_mirror_cache: Optional[GitMirrorCache] = None


def get_git_mirror_cache() -> GitMirrorCache:
    global _git_mirror_cache
    if _git_mirror_cache is None:
        _git_mirror_cache = GitMirrorCache()
    return _git_mirror_cache


def set_git_mirror_cache(cache: GitMirrorCache) -> None:
    global _git_mirror_cache
    _git_mirror_cache = cache
