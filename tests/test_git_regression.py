from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent.analysis.git_regression import build_regression_context_pack


class _FakeGitCache:
    def __init__(self):
        self.mirror = Path("/tmp/tarka/git/acme/payments.git")

    def ensure_mirror(self, repo: str):
        return self.mirror

    def run_git(self, _path, args, **_kwargs):
        argv = list(args)
        if argv[:2] == ["log", "main"]:
            return "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n" "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n"
        if argv[:1] == ["rev-parse"]:
            return "cccccccccccccccccccccccccccccccccccccccc\n"
        return ""

    def diff_range(self, _path, base: str, head: str, pathspec=None):
        _ = (base, head, pathspec)
        files = [
            {"status": "M", "path": "docs/runbook.md"},
            {"status": "M", "path": "src/service/handler.py"},
            {"status": "M", "path": "deploy/values.yaml"},
        ]
        patch = "\n".join(
            [
                "diff --git a/docs/runbook.md b/docs/runbook.md",
                "--- a/docs/runbook.md",
                "+++ b/docs/runbook.md",
                "@@ -1,3 +1,3 @@",
                "-old",
                "+new",
                "diff --git a/src/service/handler.py b/src/service/handler.py",
                "--- a/src/service/handler.py",
                "+++ b/src/service/handler.py",
            ]
            + [f"+line {i}" for i in range(1, 20)]
            + [
                "diff --git a/deploy/values.yaml b/deploy/values.yaml",
                "--- a/deploy/values.yaml",
                "+++ b/deploy/values.yaml",
            ]
            + [f"+cfg {i}" for i in range(1, 20)]
        )
        return files, patch

    def grep(self, _path, ref: str, pattern: str, **_kwargs):
        _ = (ref, pattern)
        return [{"path": "src/service/handler.py", "line": 42, "snippet": "timeout exceeded"}]

    def read_file(self, _path, ref: str, file_path: str):
        _ = ref
        if file_path == "src/service/handler.py":
            return "\n".join([f"handler line {i}" for i in range(1, 220)])
        if file_path == "deploy/values.yaml":
            return "\n".join([f"cfg line {i}" for i in range(1, 120)])
        if file_path == "docs/runbook.md":
            return "\n".join([f"doc line {i}" for i in range(1, 50)])
        raise Exception("missing")


def test_git_regression_context_pack_ranks_and_caps():
    cache = _FakeGitCache()
    now = datetime.now(timezone.utc)

    pack = build_regression_context_pack(
        repo="acme/payments",
        incident_start=now - timedelta(hours=1),
        incident_end=now,
        error_hints=["timeout"],
        git_cache=cache,
        max_files=2,
        max_diff_lines_per_file=5,
        max_total_diff_lines=6,
    )

    assert pack["candidate_range"]["base"] is not None
    assert pack["candidate_range"]["head"] is not None
    assert len(pack["selected_files"]) == 2
    assert pack["selected_files"][0]["path"] == "src/service/handler.py"

    total = 0
    for h in pack["diff_hunks"]:
        assert h["line_count"] <= 5
        total += h["line_count"]
    assert total <= 6
    assert len(pack["file_snippets"]) >= 1


def test_git_regression_context_pack_handles_no_candidate_range():
    class _NoCommitCache(_FakeGitCache):
        def run_git(self, _path, args, **_kwargs):
            argv = list(args)
            if argv[:2] == ["log", "main"]:
                return ""
            return super().run_git(_path, args, **_kwargs)

    now = datetime.now(timezone.utc)
    pack = build_regression_context_pack(
        repo="acme/payments",
        incident_start=now - timedelta(hours=1),
        incident_end=now,
        git_cache=_NoCommitCache(),
    )

    assert pack["candidate_range"]["base"] is None
    assert pack["candidate_range"]["head"] is None
    assert pack["selected_files"] == []
    assert pack["diff_hunks"] == []
