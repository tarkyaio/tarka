from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.providers.git_mirror_provider import GitMirrorCache


def test_mirror_dir_mapping(tmp_path):
    cache = GitMirrorCache(cache_root=tmp_path)
    assert cache.mirror_dir("acme/payments") == tmp_path / "acme" / "payments.git"


def test_ensure_mirror_clones_when_missing(tmp_path):
    cache = GitMirrorCache(cache_root=tmp_path, fetch_ttl_seconds=300)
    calls = []

    def fake_run_git(path, args, **kwargs):
        calls.append((path, list(args), kwargs))
        if list(args)[:2] == ["clone", "--mirror"]:
            mirror = Path(list(args)[3])
            mirror.mkdir(parents=True, exist_ok=True)
            (mirror / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        return ""

    cache.run_git = fake_run_git  # type: ignore[assignment]
    mirror = cache.ensure_mirror("acme/orders", "https://github.com/acme/orders.git")

    assert mirror.exists()
    assert (mirror / "HEAD").exists()
    assert any(c[1][:2] == ["clone", "--mirror"] for c in calls)
    meta = json.loads((mirror / ".tarka_mirror_meta.json").read_text(encoding="utf-8"))
    assert meta["repo"] == "acme/orders"
    assert isinstance(meta.get("last_fetch_time"), (int, float))
    assert isinstance(meta.get("last_access_time"), (int, float))


def test_ensure_mirror_fetches_when_stale(tmp_path):
    cache = GitMirrorCache(cache_root=tmp_path, fetch_ttl_seconds=60)
    mirror = cache.mirror_dir("acme/api")
    mirror.mkdir(parents=True, exist_ok=True)
    (mirror / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (mirror / ".tarka_mirror_meta.json").write_text(
        json.dumps({"last_fetch_time": time.time() - 3600}),
        encoding="utf-8",
    )

    calls = []

    def fake_run_git(path, args, **kwargs):
        calls.append(list(args))
        return ""

    cache.run_git = fake_run_git  # type: ignore[assignment]
    cache.ensure_mirror("acme/api", "https://github.com/acme/api.git")

    assert any(c[:3] == ["fetch", "-p", "origin"] for c in calls)


def test_ensure_mirror_skips_fetch_when_fresh(tmp_path):
    cache = GitMirrorCache(cache_root=tmp_path, fetch_ttl_seconds=600)
    mirror = cache.mirror_dir("acme/api")
    mirror.mkdir(parents=True, exist_ok=True)
    (mirror / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (mirror / ".tarka_mirror_meta.json").write_text(
        json.dumps({"last_fetch_time": time.time()}),
        encoding="utf-8",
    )

    calls = []

    def fake_run_git(path, args, **kwargs):
        calls.append(list(args))
        return ""

    cache.run_git = fake_run_git  # type: ignore[assignment]
    cache.ensure_mirror("acme/api", "https://github.com/acme/api.git")

    assert not any(c[:3] == ["fetch", "-p", "origin"] for c in calls)


def test_git_auth_token_falls_back_to_github_app(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    cache = GitMirrorCache(cache_root=tmp_path)
    mock_provider = MagicMock()
    mock_provider._get_installation_token.return_value = "app-installation-token"

    with patch("agent.providers.github_provider.get_github_provider", return_value=mock_provider):
        assert cache._resolve_git_auth_token() == "app-installation-token"


def test_run_git_redacts_token_in_error(monkeypatch, tmp_path):
    cache = GitMirrorCache(cache_root=tmp_path)
    secret = "ghp-secret-token-12345678901234567890"
    monkeypatch.setenv("GITHUB_TOKEN", secret)

    class FakeProc:
        returncode = 128
        stdout = ""
        stderr = f"fatal: auth failed token={secret}"

    with patch("agent.providers.git_mirror_provider.subprocess.run", return_value=FakeProc()):
        with pytest.raises(RuntimeError) as exc:
            cache.run_git(
                None,
                ["ls-remote", "https://github.com/acme/repo.git"],
                with_auth=True,
                remote_url="https://github.com/acme/repo.git",
            )

    assert secret not in str(exc.value)
    assert "[REDACTED]" in str(exc.value)


def test_ensure_mirror_concurrency_single_clone(tmp_path):
    cache = GitMirrorCache(cache_root=tmp_path, fetch_ttl_seconds=300)
    clone_calls = 0
    calls_lock = threading.Lock()
    errors = []

    def fake_run_git(path, args, **kwargs):
        nonlocal clone_calls
        argv = list(args)
        if argv[:2] == ["clone", "--mirror"]:
            with calls_lock:
                clone_calls += 1
            time.sleep(0.05)
            mirror = Path(argv[3])
            mirror.mkdir(parents=True, exist_ok=True)
            (mirror / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        return ""

    cache.run_git = fake_run_git  # type: ignore[assignment]

    def worker():
        try:
            cache.ensure_mirror("acme/service", "https://github.com/acme/service.git")
        except Exception as e:  # pragma: no cover - test should not fail
            errors.append(e)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors
    assert clone_calls == 1
