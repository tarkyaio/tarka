"""
Build compact regression context packs from a local git mirror.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from agent.providers.git_mirror_provider import GitMirrorCache, get_git_mirror_cache

_CODE_PREFIXES = ("src/", "app/", "server/", "cmd/", "internal/", "pkg/")
_DOC_PREFIXES = ("docs/", "doc/")
_CONFIG_HINTS = ("values.yaml", "chart", "deploy/", "k8s/", "helm", "config", ".tf", ".yaml", ".yml")


def build_regression_context_pack(
    *,
    repo: str,
    incident_start: datetime,
    incident_end: datetime,
    deployed_sha: Optional[str] = None,
    branch: str = "main",
    error_hints: Optional[Sequence[str]] = None,
    git_cache: Optional[GitMirrorCache] = None,
    max_files: int = 10,
    max_diff_lines_per_file: int = 200,
    max_total_diff_lines: int = 1200,
) -> Dict[str, Any]:
    """
    Build a bounded context pack for LLM regression analysis.
    """
    cache = git_cache or get_git_mirror_cache()
    mirror_path = cache.ensure_mirror(repo=repo)

    base, head, range_source, widened = _select_candidate_range(
        cache=cache,
        mirror_path=mirror_path,
        incident_start=incident_start,
        incident_end=incident_end,
        deployed_sha=deployed_sha,
        branch=branch,
    )

    if not base or not head:
        return {
            "repo": repo,
            "candidate_range": {"base": None, "head": None, "source": "none", "widened_window": widened},
            "selected_files": [],
            "diff_hunks": [],
            "file_snippets": [],
            "limits": _limits(max_files, max_diff_lines_per_file, max_total_diff_lines),
        }

    files, patch_text = cache.diff_range(mirror_path, base=base, head=head)
    if not files:
        return {
            "repo": repo,
            "candidate_range": {"base": base, "head": head, "source": range_source, "widened_window": widened},
            "selected_files": [],
            "diff_hunks": [],
            "file_snippets": [],
            "limits": _limits(max_files, max_diff_lines_per_file, max_total_diff_lines),
        }

    grep_hits, grep_lines = _collect_grep_hits(cache, mirror_path, head=head, error_hints=error_hints)

    ranked = sorted(
        files,
        key=lambda f: _file_score(
            path=str(f.get("path") or ""),
            status=str(f.get("status") or ""),
            grep_hits=int(grep_hits.get(str(f.get("path") or ""), 0)),
        ),
        reverse=True,
    )
    selected = ranked[:max_files]
    patch_by_file = _split_patch_by_file(patch_text)

    selected_files: List[Dict[str, Any]] = []
    diff_hunks: List[Dict[str, Any]] = []
    total_diff_lines = 0

    for f in selected:
        path = str(f.get("path") or "")
        status = str(f.get("status") or "")
        hits = int(grep_hits.get(path, 0))
        score = _file_score(path=path, status=status, grep_hits=hits)
        selected_files.append({"path": path, "status": status, "score": score, "grep_hits": hits})

        if total_diff_lines >= max_total_diff_lines:
            continue

        raw_lines = patch_by_file.get(path, [])
        if not raw_lines and f.get("old_path"):
            raw_lines = patch_by_file.get(str(f["old_path"]), [])

        room = max_total_diff_lines - total_diff_lines
        keep = min(max_diff_lines_per_file, room, len(raw_lines))
        if keep <= 0:
            continue
        kept_lines = raw_lines[:keep]
        total_diff_lines += len(kept_lines)
        diff_hunks.append(
            {
                "path": path,
                "line_count": len(kept_lines),
                "truncated": len(raw_lines) > keep,
                "patch": "\n".join(kept_lines),
            }
        )

    snippets: List[Dict[str, Any]] = []
    for f in selected_files:
        if len(snippets) >= 3:
            break
        path = str(f.get("path") or "")
        status = str(f.get("status") or "")
        if not path or status.startswith("D"):
            continue

        try:
            content = cache.read_file(mirror_path, ref=head, file_path=path)
        except Exception:
            continue

        focus = None
        lines = grep_lines.get(path)
        if lines:
            focus = lines[0]
        start, end, snippet = _build_snippet(content, focus_line=focus)
        snippets.append({"path": path, "start_line": start, "end_line": end, "content": snippet})

    return {
        "repo": repo,
        "candidate_range": {"base": base, "head": head, "source": range_source, "widened_window": widened},
        "selected_files": selected_files,
        "diff_hunks": diff_hunks,
        "file_snippets": snippets,
        "limits": _limits(max_files, max_diff_lines_per_file, max_total_diff_lines),
    }


def _limits(max_files: int, per_file: int, total: int) -> Dict[str, int]:
    return {"max_files": max_files, "max_diff_lines_per_file": per_file, "max_total_diff_lines": total}


def _select_candidate_range(
    *,
    cache: GitMirrorCache,
    mirror_path: Path,
    incident_start: datetime,
    incident_end: datetime,
    deployed_sha: Optional[str],
    branch: str,
) -> Tuple[Optional[str], Optional[str], str, bool]:
    widened = False
    if deployed_sha:
        head = deployed_sha.strip()
        base = _safe_rev_parse(cache, mirror_path, f"{head}^") or _safe_rev_parse(cache, mirror_path, branch)
        return base, head, "deployed_sha", widened

    shas = _log_shas(cache, mirror_path, branch=branch, since=incident_start, until=incident_end)
    if not shas:
        widened = True
        shas = _log_shas(
            cache, mirror_path, branch=branch, since=incident_start - timedelta(hours=24), until=incident_end
        )
    if not shas:
        return None, None, "none", widened

    head = shas[0]
    if len(shas) >= 2:
        base = shas[-1]
    else:
        base = _safe_rev_parse(cache, mirror_path, f"{head}^") or head
    return base, head, "time_window", widened


def _log_shas(
    cache: GitMirrorCache,
    mirror_path: Path,
    *,
    branch: str,
    since: datetime,
    until: datetime,
) -> List[str]:
    out = cache.run_git(
        mirror_path,
        [
            "log",
            branch,
            f"--since={since.isoformat()}",
            f"--until={until.isoformat()}",
            "--pretty=format:%H",
        ],
        allow_exit_codes=[0],
    )
    return [line.strip() for line in out.splitlines() if line.strip()]


def _safe_rev_parse(cache: GitMirrorCache, mirror_path: Path, expr: str) -> Optional[str]:
    try:
        out = cache.run_git(mirror_path, ["rev-parse", expr])
        value = (out or "").strip()
        return value or None
    except Exception:
        return None


def _collect_grep_hits(
    cache: GitMirrorCache,
    mirror_path: Path,
    *,
    head: str,
    error_hints: Optional[Sequence[str]],
) -> Tuple[Dict[str, int], Dict[str, List[int]]]:
    path_hits: Dict[str, int] = {}
    path_lines: Dict[str, List[int]] = {}
    hints = [str(h).strip() for h in (error_hints or []) if str(h).strip()]
    for hint in hints[:5]:
        if len(hint) < 3:
            continue
        try:
            matches = cache.grep(mirror_path, ref=head, pattern=hint, max_results=250)
        except Exception:
            continue
        for m in matches:
            path = str(m.get("path") or "")
            if not path:
                continue
            path_hits[path] = path_hits.get(path, 0) + 1
            line = int(m.get("line") or 0)
            if line > 0:
                path_lines.setdefault(path, []).append(line)
    return path_hits, path_lines


def _file_score(*, path: str, status: str, grep_hits: int) -> int:
    p = (path or "").lower()
    score = 0
    if p.startswith(_CODE_PREFIXES):
        score += 6
    if any(h in p for h in _CONFIG_HINTS):
        score += 4
    if p.startswith(_DOC_PREFIXES) or p.endswith(".md"):
        score -= 5
    if status and status[0] in {"M", "A", "R"}:
        score += 1
    score += min(grep_hits * 3, 18)
    return score


def _split_patch_by_file(patch_text: str) -> Dict[str, List[str]]:
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


def _build_snippet(content: str, *, focus_line: Optional[int]) -> Tuple[int, int, str]:
    lines = (content or "").splitlines()
    if not lines:
        return 1, 1, ""

    if focus_line and focus_line > 0:
        start = max(1, focus_line - 20)
        end = min(len(lines), focus_line + 20)
    else:
        start = 1
        end = min(len(lines), 80)

    snippet = "\n".join(lines[start - 1 : end])
    if len(snippet) > 4000:
        snippet = snippet[:4000]
    return start, end, snippet
