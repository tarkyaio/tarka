from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, Iterable, Literal, Optional, Tuple

ToolOutcome = Literal["ok", "empty", "unavailable", "error", "skipped_duplicate"]


def _truncate(s: str, n: int) -> str:
    txt = (s or "").strip()
    if len(txt) <= n:
        return txt
    return txt[: max(0, n - 1)].rstrip() + "…"


def _jsonable(v: Any, *, _depth: int = 0, _max_depth: int = 6) -> Any:
    """
    Best-effort convert values to JSON-serializable objects for stable keying.
    """
    if _depth >= _max_depth:
        return str(v)
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, datetime):
        try:
            return v.isoformat()
        except Exception:
            return str(v)
    if isinstance(v, dict):
        out: Dict[str, Any] = {}
        for k, vv in v.items():
            out[str(k)] = _jsonable(vv, _depth=_depth + 1, _max_depth=_max_depth)
        return out
    if isinstance(v, (list, tuple, set)):
        return [_jsonable(x, _depth=_depth + 1, _max_depth=_max_depth) for x in list(v)]
    try:
        # Pydantic models / objects with dict() (best-effort)
        if hasattr(v, "model_dump"):
            return _jsonable(v.model_dump(mode="json"), _depth=_depth + 1, _max_depth=_max_depth)
        if hasattr(v, "dict"):
            return _jsonable(v.dict(), _depth=_depth + 1, _max_depth=_max_depth)
    except Exception:
        pass
    return str(v)


def normalize_tool_args(tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize args for stable dedupe keys (order-insensitive, jsonable).
    """
    _ = tool  # reserved for per-tool normalization later
    out = _jsonable(args or {})
    return out if isinstance(out, dict) else {"_": out}


def tool_call_key(tool: str, args: Dict[str, Any]) -> str:
    """
    Short stable fingerprint for (tool, normalized_args).
    """
    norm = normalize_tool_args(tool, args)
    payload = json.dumps(
        {"tool": str(tool or "").strip(), "args": norm}, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    # Short, readable key; not intended for cryptographic use.
    h = hashlib.blake2s(payload.encode("utf-8"), digest_size=6).hexdigest()  # 12 hex chars
    return f"{str(tool or '').strip()}:{h}"


def compact_args_for_prompt(args: Dict[str, Any], *, max_keys: int = 8, max_value_chars: int = 80) -> Dict[str, Any]:
    """
    Keep prompts small while still letting the model see what was called.
    """
    if not isinstance(args, dict):
        return {}
    out: Dict[str, Any] = {}
    for i, (k, v) in enumerate(args.items()):
        if i >= max_keys:
            break
        kk = str(k)
        vv = _jsonable(v)
        if isinstance(vv, str):
            vv = _truncate(vv, max_value_chars)
        out[kk] = vv
    return out


def _count_list(x: Any) -> Optional[int]:
    return len(x) if isinstance(x, list) else None


def _get(d: Any, *path: str) -> Any:
    cur = d
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _first_nonempty(xs: Iterable[Any]) -> Optional[Any]:
    for x in xs:
        if x is None:
            continue
        s = str(x).strip()
        if s:
            return x
    return None


def summarize_tool_result(*, tool: str, ok: bool, error: Optional[str], result: Any) -> Tuple[ToolOutcome, str]:
    """
    Return (outcome, summary) for prompts/UI.
    """
    t = str(tool or "").strip()
    if (not ok) or (error is not None and str(error).strip()):
        return "error", _truncate(f"{t}: error {str(error or '').strip() or 'unknown'}", 160)

    # Common structured shape: {status: "..."}
    if isinstance(result, dict):
        status = str(result.get("status") or "").strip().lower()
        if status in ("unavailable",):
            reason = str(result.get("reason") or "").strip()
            base = f"{t}: unavailable"
            if reason:
                base += f" (reason={reason})"
            return "unavailable", _truncate(base, 160)

        # logs.tail (victorialogs)
        if t == "logs.tail":
            entries = result.get("entries")
            n = _count_list(entries) or 0
            st = status if status in ("ok", "empty", "unavailable") else ("empty" if n == 0 else "ok")
            reason = str(result.get("reason") or "").strip()
            backend = str(result.get("backend") or "").strip()
            q = str(result.get("query_used") or "").strip()
            parts = [f"logs: {st} ({n} entries)"]
            if reason and st != "ok":
                parts.append(f"reason={reason}")
            if backend:
                parts.append(f"backend={backend}")
            if q and st != "ok":
                parts.append(f"query={_truncate(q, 64)}")
            out = "; ".join(parts)
            return ("empty" if st == "empty" else ("unavailable" if st == "unavailable" else "ok")), _truncate(out, 160)

        # promql.instant
        if t == "promql.instant":
            series = result.get("result")
            n = _count_list(series)
            q = str(result.get("query") or "").strip()
            if n == 0:
                return "empty", _truncate(f"promql: empty (0 series) query={_truncate(q, 80)}", 160)
            if n is not None:
                return "ok", _truncate(f"promql: ok ({n} series) query={_truncate(q, 80)}", 160)

        # memory.*
        if t in ("memory.similar_cases", "memory.skills"):
            items = result.get("items")
            n = _count_list(items)
            label = "similar_cases" if t.endswith("similar_cases") else "skills"
            if n == 0:
                return "empty", _truncate(f"memory: {label} empty (0)", 160)
            if n is not None:
                return "ok", _truncate(f"memory: {label} ok ({n})", 160)

        # k8s.pod_context
        if t == "k8s.pod_context":
            pi = result.get("pod_info") if isinstance(result.get("pod_info"), dict) else {}
            phase = str((pi or {}).get("phase") or "").strip().lower() or None
            reason = str((pi or {}).get("status_reason") or "").strip() or None
            statuses = (
                (pi or {}).get("container_statuses") if isinstance((pi or {}).get("container_statuses"), list) else []
            )
            restarts = 0
            not_ready = 0
            for cs in statuses:
                if not isinstance(cs, dict):
                    continue
                try:
                    restarts += int(cs.get("restart_count") or 0)
                except Exception:
                    pass
                if cs.get("ready") is False:
                    not_ready += 1
            evs = result.get("pod_events") if isinstance(result.get("pod_events"), list) else []
            errs = result.get("errors") if isinstance(result.get("errors"), list) else []
            parts = ["k8s: pod_context ok"]
            if phase:
                parts.append(f"phase={phase}")
            if reason:
                parts.append(f"reason={_truncate(reason, 32)}")
            if statuses:
                parts.append(f"not_ready={not_ready}")
                parts.append(f"restarts={restarts}")
            if evs:
                parts.append(f"events={len(evs)}")
            if errs:
                parts.append(f"errors={len(errs)}")
            return "ok", _truncate("; ".join(parts), 160)

        if t == "k8s.rollout_status":
            kind = str(result.get("kind") or "").strip()
            name = str(result.get("name") or "").strip()
            # Try a few common fields across kinds.
            ready = _first_nonempty([result.get("ready_replicas"), result.get("number_ready")])
            desired = _first_nonempty([result.get("replicas"), result.get("desired_number_scheduled")])
            parts = ["k8s: rollout_status ok"]
            if kind and name:
                parts.append(f"{kind}/{name}")
            if desired is not None or ready is not None:
                parts.append(f"ready={ready}/{desired}")
            return "ok", _truncate("; ".join([str(x) for x in parts if x is not None]), 160)

        # github.*
        if t == "github.recent_commits":
            commits = result.get("commits")
            n = _count_list(commits)
            total = result.get("total_available")
            repo = str(result.get("repo") or "").strip()
            window_h = result.get("searched_window_hours")
            window_note = f" in last {window_h}h" if window_h else ""
            if n == 0:
                return "empty", _truncate(f"github: 0 commits{window_note} for {repo}", 160)
            if n is not None:
                suffix = f" (of {total} available)" if total is not None and total > n else ""
                return "ok", _truncate(f"github: {n} commits{suffix}{window_note} for {repo}", 160)

        if t == "github.workflow_runs":
            runs = result.get("workflow_runs")
            n = _count_list(runs)
            repo = str(result.get("repo") or "").strip()
            if n == 0:
                return "empty", _truncate(f"github: 0 workflow runs for {repo}", 160)
            if n is not None:
                # Count failures
                failures = sum(1 for r in runs if isinstance(r, dict) and r.get("conclusion") == "failure")
                parts = [f"github: {n} workflow runs for {repo}"]
                if failures:
                    parts.append(f"{failures} failed")
                return "ok", _truncate("; ".join(parts), 160)

        if t == "github.commit_diff":
            sha = str(result.get("sha") or "").strip()
            files = result.get("files")
            n = _count_list(files)
            repo = str(result.get("repo") or "").strip()
            if n is not None and n > 0:
                filenames = [f.get("filename", "") for f in files[:3] if isinstance(f, dict)]
                parts = [f"github: commit {sha} changed {n} files"]
                if filenames:
                    parts.append(f"({', '.join(filenames)}{'...' if n > 3 else ''})")
                return "ok", _truncate(" ".join(parts), 160)
            if n == 0:
                return "empty", _truncate(f"github: commit {sha} changed 0 files", 160)

        # Global tools (inbox)
        if t == "cases.count":
            try:
                n = int(result.get("count") or 0)
            except Exception:
                n = None
            if n == 0:
                return "empty", _truncate("cases.count: empty (0)", 160)
            if n is not None:
                return "ok", _truncate(f"cases.count: ok ({n})", 160)

        if t == "cases.top":
            items = result.get("items")
            n = _count_list(items)
            by = str(result.get("by") or "").strip() or "key"
            if n == 0:
                return "empty", _truncate(f"cases.top: empty (by={by})", 160)
            if n is not None:
                top_key = None
                if isinstance(items, list) and items and isinstance(items[0], dict):
                    top_key = items[0].get("key")
                return "ok", _truncate(f"cases.top: ok ({n} buckets) by={by} top={top_key}", 160)

        if t == "cases.lookup":
            matches = result.get("matches")
            n = _count_list(matches)
            if n == 0:
                return "empty", _truncate("cases.lookup: empty (0 matches)", 160)
            if n is not None:
                return "ok", _truncate(f"cases.lookup: ok ({n} matches)", 160)

        if t == "cases.summary":
            found = result.get("found")
            if found is False:
                return "empty", _truncate("cases.summary: empty (not found)", 160)
            if found is True:
                return "ok", _truncate("cases.summary: ok (found)", 160)

        # Generic status mapping
        if status in ("empty",):
            return "empty", _truncate(f"{t}: empty", 160)
        if status in ("ok",):
            return "ok", _truncate(f"{t}: ok", 160)

        # Generic list counters
        for list_key in ("items", "entries", "result"):
            n = _count_list(result.get(list_key))
            if n == 0:
                return "empty", _truncate(f"{t}: empty (0 {list_key})", 160)
            if n is not None:
                return "ok", _truncate(f"{t}: ok ({n} {list_key})", 160)

    # Fallback
    return "ok", _truncate(f"{t}: ok", 160)
