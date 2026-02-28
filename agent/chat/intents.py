from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

from agent.authz.policy import ChatPolicy
from agent.chat.global_tools import run_global_tool
from agent.chat.types import ChatToolEvent
from agent.memory.config import build_postgres_dsn, load_memory_config

Scope = Literal["case", "global"]


@dataclass(frozen=True)
class IntentResult:
    handled: bool
    reply: str = ""
    tool_events: List[ChatToolEvent] = None  # type: ignore[assignment]
    intent_id: Optional[str] = None

    def __post_init__(self):
        if self.tool_events is None:
            object.__setattr__(self, "tool_events", [])


def _connect(dsn: str):
    import psycopg  # type: ignore[import-not-found]

    return psycopg.connect(dsn)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _parse_days_window(s: str) -> Optional[int]:
    m = re.search(r"\blast\s+(\d+)\s*day", s)
    if not m:
        m = re.search(r"\bpast\s+(\d+)\s*day", s)
    if not m:
        m = re.search(r"\b(\d+)\s*d\b", s)
    if not m:
        return None
    try:
        n = int(m.group(1))
    except Exception:
        return None
    return max(1, min(n, 30))


def _hash_question(s: str) -> str:
    return hashlib.sha256(_norm(s).encode("utf-8")).hexdigest()


def _extract_token(s: str, key: str) -> Optional[str]:
    """
    Extract naive 'key=value' or 'key: value' tokens from a user message.
    """
    m = re.search(rf"\b{re.escape(key)}\s*[:=]\s*([a-z0-9_\-]+)\b", s)
    if not m:
        return None
    return m.group(1)


# ---------------------------------------------------------------------------
# Fast-path patterns (anchored: must match the ENTIRE normalised message)
# ---------------------------------------------------------------------------

_GREETING_PATTERNS = re.compile(
    r"^(hi|hey|hello|howdy|yo|sup|greetings|good\s+(morning|afternoon|evening)|"
    r"thanks|thank\s*you|thx|ty|cheers|cool|ok|okay|got\s*it|understood|"
    r"sounds\s*good|makes\s*sense|perfect|great|awesome|nice|noted|"
    r"bye|goodbye|see\s*ya|later|good\s*night)[.!?\s]*$",
    re.IGNORECASE,
)

_SUMMARY_PATTERNS = re.compile(
    r"^(what\s+happened|what'?s?\s+(going\s+on|the\s+(issue|problem|situation|story|deal))|"
    r"summarize|summary|tldr|tl;?dr|overview|recap|brief\s*me|catch\s*me\s*up|"
    r"give\s*me\s*(the\s+)?(summary|tldr|overview|rundown|gist)|"
    r"explain\s+(this|the)\s+(case|alert|incident|issue))[.!?\s]*$",
    re.IGNORECASE,
)

_STATUS_PATTERNS = re.compile(
    r"^(what'?s?\s+(the\s+)?status|is\s+(it|this)\s+(resolved|fixed|still\s+(firing|active|down|broken))|"
    r"still\s+(happening|firing|active|down|broken)|"
    r"are\s+we\s+(ok|good|safe|fine)|"
    r"how\s+bad\s+is\s+(it|this))[.!?\s]*$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers: build deterministic replies from analysis_json
# ---------------------------------------------------------------------------


def _build_case_summary(analysis_json: Dict[str, Any]) -> str:
    """Build a concise summary reply from the investigation SSOT."""
    tgt = analysis_json.get("target") if isinstance(analysis_json.get("target"), dict) else {}
    a = analysis_json.get("analysis") if isinstance(analysis_json.get("analysis"), dict) else {}
    verdict = a.get("verdict") if isinstance(a.get("verdict"), dict) else {}
    hyps = a.get("hypotheses") if isinstance(a.get("hypotheses"), list) else []

    target_name = tgt.get("name") or tgt.get("service") or "this target"
    ns = tgt.get("namespace")
    label = verdict.get("label") or "No verdict available yet."

    parts = [f"**{target_name}**" + (f" (ns: {ns})" if ns else "") + f": {label}"]

    if hyps:
        parts.append("Top hypotheses:")
        for h in hyps[:3]:
            if isinstance(h, dict):
                title = h.get("title") or h.get("hypothesis_id") or "unknown"
                conf = h.get("confidence_0_100", "?")
                parts.append(f"- {title} ({conf}/100)")

    nexts = verdict.get("next") if isinstance(verdict.get("next"), list) else []
    if nexts:
        parts.append("Suggested next steps:")
        for n in nexts[:3]:
            parts.append(f"- {n}")

    return "\n".join(parts)


def _build_status_reply(analysis_json: Dict[str, Any]) -> str:
    """Build a status reply from the investigation SSOT."""
    tgt = analysis_json.get("target") if isinstance(analysis_json.get("target"), dict) else {}
    a = analysis_json.get("analysis") if isinstance(analysis_json.get("analysis"), dict) else {}
    verdict = a.get("verdict") if isinstance(a.get("verdict"), dict) else {}
    scores = a.get("scores") if isinstance(a.get("scores"), dict) else {}

    target_name = tgt.get("name") or tgt.get("service") or "this target"
    label = verdict.get("label") or "No verdict available."
    classification = scores.get("classification") or "unknown"
    severity = scores.get("severity") or "unknown"
    confidence = scores.get("confidence")

    parts = [
        f"**{target_name}** — {label}",
        f"Classification: **{classification}** | Severity: **{severity}**"
        + (f" | Confidence: {confidence}/100" if confidence is not None else ""),
    ]

    parts.append("If you'd like fresh data, ask me to re-check with live tools.")

    return "\n".join(parts)


_FAMILY_SYNONYMS = {
    "cpu throttling": "cpu_throttling",
    "cpu_throttling": "cpu_throttling",
    "oom": "oom_killed",
    "oomkilled": "oom_killed",
    "oom killed": "oom_killed",
    "http 5xx": "http_5xx",
    "5xx": "http_5xx",
}


def _infer_family(s: str) -> Optional[str]:
    for k, v in _FAMILY_SYNONYMS.items():
        if k in s:
            return v
    fam = _extract_token(s, "family")
    return fam


def _infer_team(s: str) -> Optional[str]:
    t = _extract_token(s, "team")
    if t:
        return t
    m = re.search(r"\bfor\s+team\s+([a-z0-9_\-]+)\b", s)
    if m:
        return m.group(1)
    return None


def _infer_classification(s: str) -> Optional[str]:
    cls = _extract_token(s, "classification")
    if cls:
        return cls
    if "noise" in s or "noisy" in s:
        return "noisy"
    if "actionable" in s:
        return "actionable"
    if "informational" in s:
        return "informational"
    return None


def try_handle_global_intents(*, policy: ChatPolicy, user_message: str) -> IntentResult:
    """
    Deterministic intents for GLOBAL chat. Safe, cheap, and expandable.
    """
    s = _norm(user_message)
    if not s:
        return IntentResult(handled=False)

    # Intent: global.greeting — warm reply, zero tools
    if _GREETING_PATTERNS.match(s):
        return IntentResult(
            handled=True,
            reply="Hey! I'm here to help you explore the incident database. What would you like to know?",
            intent_id="global.greeting",
        )

    # Intent: cases.count
    if ("how many" in s or s.startswith("count ") or " count " in s) and ("case" in s or "cases" in s or True):
        fam = _infer_family(s)
        team = _infer_team(s)
        cls = _infer_classification(s)
        days = _parse_days_window(s)
        args: Dict[str, Any] = {"status": "all"}
        if fam:
            args["family"] = fam
        if team:
            args["team"] = team
        if cls:
            args["classification"] = cls
        if days:
            args["since_hours"] = int(days) * 24

        res = run_global_tool(policy=policy, tool="cases.count", args=args)
        ev = ChatToolEvent(tool="cases.count", args=args, ok=bool(res.ok), result=res.result, error=res.error)
        if not res.ok:
            return IntentResult(
                handled=True,
                reply="I couldn't query counts right now (db/tool unavailable).",
                tool_events=[ev],
                intent_id="global.cases_count",
            )
        count = (res.result or {}).get("count") if isinstance(res.result, dict) else None
        return IntentResult(
            handled=True,
            reply=f"Count: **{count}** case(s).",
            tool_events=[ev],
            intent_id="global.cases_count",
        )

    # Intent: cases.top
    if "top teams" in s or "which teams" in s:
        args = {"by": "team", "status": "all", "limit": 8}
        res = run_global_tool(policy=policy, tool="cases.top", args=args)
        ev = ChatToolEvent(tool="cases.top", args=args, ok=bool(res.ok), result=res.result, error=res.error)
        if not res.ok:
            return IntentResult(
                handled=True,
                reply="I couldn't compute top teams right now (db/tool unavailable).",
                tool_events=[ev],
                intent_id="global.cases_top_team",
            )
        items = (res.result or {}).get("items") if isinstance(res.result, dict) else []
        lines = []
        for it in (items or [])[:8]:
            if isinstance(it, dict):
                lines.append(f"- {it.get('key')}: {it.get('count')}")
        return IntentResult(
            handled=True,
            reply="Top teams by case count:\n" + ("\n".join(lines) if lines else "—"),
            tool_events=[ev],
            intent_id="global.cases_top_team",
        )

    return IntentResult(handled=False)


def try_handle_case_intents(*, analysis_json: Dict[str, Any], user_message: str) -> IntentResult:
    """
    Deterministic intents for CASE chat. These should be rare and high-signal.
    """
    s = _norm(user_message)
    if not s:
        return IntentResult(handled=False)

    # Intent: case.greeting — warm reply referencing the target, zero tools
    if _GREETING_PATTERNS.match(s):
        tgt = analysis_json.get("target") if isinstance(analysis_json.get("target"), dict) else {}
        target_name = tgt.get("name") or tgt.get("service") or "this case"
        return IntentResult(
            handled=True,
            reply=f"Hey! I'm here to help with **{target_name}**. What would you like to know?",
            intent_id="case.greeting",
        )

    # Intent: case.summary — build reply from analysis_json, zero tools
    if _SUMMARY_PATTERNS.match(s):
        return IntentResult(
            handled=True,
            reply=_build_case_summary(analysis_json),
            intent_id="case.summary",
        )

    # Intent: case.status — build reply from verdict/classification, zero tools
    if _STATUS_PATTERNS.match(s):
        return IntentResult(
            handled=True,
            reply=_build_status_reply(analysis_json),
            intent_id="case.status",
        )

    # Intent: family count over N days, scoped to the target in analysis_json.
    # This covers questions like:
    # - "how many times did this app get OOM killed in the last 7 days"
    # - "how many CPU throttling cases/runs did this service have in the last 14 days"
    if ("how many" in s or "count" in s) and ("last" in s or "past" in s):
        family = _infer_family(s)
        if not family:
            return IntentResult(handled=False)

        days = _parse_days_window(s) or 7
        tgt = analysis_json.get("target") if isinstance(analysis_json.get("target"), dict) else {}
        service = (tgt.get("service") if isinstance(tgt, dict) else None) or None
        namespace = (tgt.get("namespace") if isinstance(tgt, dict) else None) or None
        cluster = (tgt.get("cluster") if isinstance(tgt, dict) else None) or None

        cfg = load_memory_config()
        dsn = build_postgres_dsn(cfg)
        if not dsn:
            return IntentResult(
                handled=True,
                reply="I can’t answer that right now because Postgres isn’t configured (it’s required for historical counts).",
                tool_events=[],
                intent_id="case.family_db_count",
            )

        # For OOMKilled, we support an additional boolean fallback in analysis_json for robustness.
        family_pred = "LOWER(COALESCE(family, '')) = LOWER(%s)"
        family_params: List[Any] = [str(family)]
        if family == "oom_killed":
            family_pred = f"({family_pred} OR LOWER(COALESCE(oom_flag, '')) IN ('true', '1', 'yes'))"

        with _connect(dsn) as conn:
            row = conn.execute(
                f"""
                WITH scoped AS (
                  SELECT
                    r.run_id,
                    r.case_id,
                    r.created_at,
                    NULLIF(r.analysis_json #>> '{{analysis,features,family}}', '') as family,
                    NULLIF(r.analysis_json #>> '{{analysis,features,k8s,oom_killed}}', '') as oom_flag,
                    NULLIF(r.analysis_json #>> '{{target,service}}', '') as svc,
                    NULLIF(r.analysis_json #>> '{{target,namespace}}', '') as ns,
                    NULLIF(r.analysis_json #>> '{{target,cluster}}', '') as cl
                  FROM investigation_runs r
                  WHERE r.created_at >= (now() - (%s::int * interval '1 day'))
                )
                SELECT
                  COUNT(*)::int as runs_count,
                  COUNT(DISTINCT case_id)::int as cases_count
                FROM scoped
                WHERE
                  {family_pred}
                  AND (%s::text IS NULL OR svc = %s::text)
                  AND (%s::text IS NULL OR ns = %s::text)
                  AND (%s::text IS NULL OR cl = %s::text);
                """,
                (
                    int(days),
                    *family_params,
                    str(service) if service else None,
                    str(service) if service else None,
                    str(namespace) if namespace else None,
                    str(namespace) if namespace else None,
                    str(cluster) if cluster else None,
                    str(cluster) if cluster else None,
                ),
            ).fetchone()
            runs_count = int(row[0] or 0) if row else 0
            cases_count = int(row[1] or 0) if row else 0

        svc_label = f"service `{service}`" if service else "this target"
        return IntentResult(
            handled=True,
            reply=(
                f"Last {days} days: **{runs_count}** run(s) across **{cases_count}** case(s) for family `{family}` on {svc_label}."
                " (Count is from the case database, not pod restart counters.)"
            ),
            tool_events=[],
            intent_id="case.family_db_count",
        )

    return IntentResult(handled=False)
