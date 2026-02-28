from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from agent.core.models import Investigation
from agent.memory.config import build_postgres_dsn, load_memory_config


@dataclass(frozen=True)
class Skill:
    skill_id: str
    name: str
    version: int
    when_json: Dict[str, Any]
    template: str


@dataclass(frozen=True)
class SkillMatch:
    skill: Skill
    rendered: str
    match_reason: str = "matched"


def _connect(dsn: str):
    import psycopg  # type: ignore[import-not-found]

    return psycopg.connect(dsn)


def build_skill_context(investigation: Investigation) -> Dict[str, Any]:
    features = (
        investigation.analysis.features.model_dump(mode="json") if investigation.analysis.features is not None else {}
    )
    verdict = (
        investigation.analysis.verdict.model_dump(mode="json") if investigation.analysis.verdict is not None else {}
    )
    scores = investigation.analysis.scores.model_dump(mode="json") if investigation.analysis.scores is not None else {}
    target = investigation.target.model_dump(mode="json")
    return {
        "features": features,
        "verdict": verdict,
        "scores": scores,
        "target": target,
        "alert": {
            "fingerprint": investigation.alert.fingerprint,
            "labels": investigation.alert.labels or {},
            "annotations": investigation.alert.annotations or {},
            "normalized_state": investigation.alert.normalized_state,
            "starts_at": investigation.alert.starts_at,
        },
    }


def _get_path(ctx: Dict[str, Any], path: str) -> Any:
    cur: Any = ctx
    for part in (path or "").split("."):
        if part == "":
            continue
        if isinstance(cur, dict) and part in cur:
            cur = cur.get(part)
        else:
            return None
    return cur


def eval_when(when_json: Any, ctx: Dict[str, Any]) -> bool:
    """
    Evaluate a constrained predicate JSON.

    Supported operators:
    - {"all":[...]} / {"any":[...]} / {"not": {...}}
    - {"exists":"a.b.c"}
    - {"eq":["a.b", 123]} / {"ne":[...]}
    - {"gt":["a.b", 1]} / {"gte":[...]} / {"lt":[...]} / {"lte":[...]}
    - {"contains":["a.b", "substr"]}  (string contains or list contains)
    """
    if when_json is None:
        return False
    if isinstance(when_json, bool):
        return when_json
    if not isinstance(when_json, dict):
        return False

    if "all" in when_json:
        xs = when_json.get("all") or []
        return all(eval_when(x, ctx) for x in xs)
    if "any" in when_json:
        xs = when_json.get("any") or []
        return any(eval_when(x, ctx) for x in xs)
    if "not" in when_json:
        return not eval_when(when_json.get("not"), ctx)

    if "exists" in when_json:
        v = _get_path(ctx, str(when_json.get("exists") or ""))
        return v is not None

    def _bin(op: str) -> Optional[Tuple[Any, Any]]:
        raw = when_json.get(op)
        if not isinstance(raw, list) or len(raw) != 2:
            return None
        left = _get_path(ctx, str(raw[0] or ""))
        right = raw[1]
        return left, right

    for op in ("eq", "ne", "gt", "gte", "lt", "lte", "contains"):
        if op not in when_json:
            continue
        pair = _bin(op)
        if pair is None:
            return False
        left, right = pair
        if op == "eq":
            return left == right
        if op == "ne":
            return left != right
        try:
            if op == "gt":
                return left is not None and float(left) > float(right)
            if op == "gte":
                return left is not None and float(left) >= float(right)
            if op == "lt":
                return left is not None and float(left) < float(right)
            if op == "lte":
                return left is not None and float(left) <= float(right)
        except Exception:
            return False
        if op == "contains":
            if left is None:
                return False
            if isinstance(left, str):
                return str(right) in left
            if isinstance(left, list):
                return right in left
            return False

    # Unknown operator
    return False


_TPL_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}")


def render_template(template: str, ctx: Dict[str, Any]) -> str:
    """
    Render a tiny template language: {{path.to.value}}.
    """

    def repl(m: re.Match) -> str:
        path = m.group(1)
        v = _get_path(ctx, path)
        if v is None:
            return ""
        if isinstance(v, (dict, list)):
            try:
                return json.dumps(v, sort_keys=True)
            except Exception:
                return str(v)
        return str(v)

    return _TPL_RE.sub(repl, template or "")


def load_active_skills() -> Tuple[bool, str, List[Skill]]:
    cfg = load_memory_config()
    dsn = build_postgres_dsn(cfg)
    if not dsn:
        return False, "Postgres not configured", []
    with _connect(dsn) as conn:
        rows = conn.execute("""
            SELECT skill_id, name, version, when_json, template
            FROM skills
            WHERE status = 'active'
            ORDER BY name, version;
            """).fetchall()
    skills: List[Skill] = []
    for r in rows or []:
        skills.append(
            Skill(
                skill_id=str(r[0]),
                name=str(r[1]),
                version=int(r[2]),
                when_json=r[3] if isinstance(r[3], dict) else {},
                template=str(r[4] or ""),
            )
        )
    return True, "ok", skills


def match_skills(investigation: Investigation, *, max_matches: int = 5) -> Tuple[bool, str, List[SkillMatch]]:
    ok, msg, skills = load_active_skills()
    if not ok:
        return False, msg, []
    ctx = build_skill_context(investigation)
    matches: List[SkillMatch] = []
    for s in skills:
        if eval_when(s.when_json, ctx):
            matches.append(SkillMatch(skill=s, rendered=render_template(s.template, ctx)))
        if len(matches) >= max_matches:
            break
    return True, "ok", matches
