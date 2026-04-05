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


def _is_token_cost_question(s: str) -> bool:
    """Keyword check: is the user asking about LLM token usage or investigation cost?"""
    if "token" in s or "llm" in s:
        return True
    if "cost" in s and ("this" in s or "investigation" in s or "case" in s or "run" in s):
        return True
    return False


_STATUS_PATTERNS = re.compile(
    r"^(what'?s?\s+(the\s+)?status|is\s+(it|this)\s+(resolved|fixed|still\s+(firing|active|down|broken))|"
    r"still\s+(happening|firing|active|down|broken)|"
    r"are\s+we\s+(ok|good|safe|fine)|"
    r"how\s+bad\s+is\s+(it|this))[.!?\s]*$",
    re.IGNORECASE,
)

_GLOBAL_STATUS_CHECK_PATTERNS = re.compile(
    r"^(any\s+fires?|how'?s?\s+it\s+(look|going)|how\s+does\s+it\s+look|"
    r"what'?s?\s+(going\s+on\s+(today|now)|up\s+today)|"
    r"how\s+are\s+we\s+doing|status\s+(check|update)|"
    r"anything\s+(going\s+on|broken|on\s+fire))[.!?\s]*$",
    re.IGNORECASE,
)

# Effective-status keywords — must be checked before the generic cases.count handler.
_EFFECTIVE_STATUS_RE = re.compile(r"\b(stale|snoozed?|firing|resolved|closed)\b", re.IGNORECASE)

# Recent-cases patterns — "show me recent cases", "what just came in", "latest alerts"
_RECENT_CASES_RE = re.compile(
    r"\b(recent|latest|newest|just\s+came\s+in|last\s+few|new\s+alert|new\s+case)\b", re.IGNORECASE
)

# Severity patterns — "how many critical", "any severity=critical"
_SEVERITY_RE = re.compile(r"\b(critical|warning|high\s+severity|severity\s*[=:]\s*critical)\b", re.IGNORECASE)

# Trending patterns — "what's trending", "is it getting worse", "compare to yesterday"
_TRENDING_RE = re.compile(
    r"\b(trend|trending|getting\s+worse|getting\s+better|compare\s+to\s+(yesterday|last\s+(week|hour|day))|"
    r"worse\s+than\s+before|worse\s+than\s+yesterday)\b",
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

    parts = [f"**{target_name}**" + (f" ({ns})" if ns else "") + f" — {label}"]

    if hyps:
        parts.append("Top suspects:")
        for h in hyps[:3]:
            if isinstance(h, dict):
                title = h.get("title") or h.get("hypothesis_id") or "unknown"
                conf = h.get("confidence_0_100", "?")
                parts.append(f"- {title} ({conf}/100)")

    nexts = verdict.get("next") if isinstance(verdict.get("next"), list) else []
    if nexts:
        parts.append("What to do next:")
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

    parts.append("Want me to pull fresh data on this?")

    return "\n".join(parts)


def _build_token_usage_reply(analysis_json: Dict[str, Any]) -> Optional[str]:
    """Build a deterministic reply about LLM token usage from the SSOT. Returns None if no data."""
    a = analysis_json.get("analysis") if isinstance(analysis_json.get("analysis"), dict) else {}
    llm_info = a.get("llm") if isinstance(a.get("llm"), dict) else {}
    rca_info = a.get("rca") if isinstance(a.get("rca"), dict) else {}
    llm_usage = llm_info.get("usage") if isinstance(llm_info.get("usage"), dict) else {}
    rca_usage = rca_info.get("usage") if isinstance(rca_info.get("usage"), dict) else {}

    if not llm_usage and not rca_usage:
        return None

    parts: List[str] = []
    total_tokens = 0
    total_cost = 0.0

    if llm_usage:
        tokens = llm_usage.get("total_tokens") or 0
        cost = llm_usage.get("estimated_cost_usd") or 0
        model = llm_info.get("model")
        total_tokens += tokens
        total_cost += cost
        label = f"**Enrichment** ({model})" if model else "**Enrichment**"
        parts.append(f"- {label}: {tokens:,} tokens" + (f" — ${cost:.4f}" if cost else ""))

    if rca_usage:
        tokens = rca_usage.get("total_tokens") or 0
        cost = rca_usage.get("estimated_cost_usd") or 0
        model = rca_info.get("model")
        total_tokens += tokens
        total_cost += cost
        label = f"**RCA** ({model})" if model else "**RCA**"
        parts.append(f"- {label}: {tokens:,} tokens" + (f" — ${cost:.4f}" if cost else ""))

    header = f"**Total: {total_tokens:,} tokens" + (f" — ${total_cost:.4f}**" if total_cost else "**")
    return header + "\n" + "\n".join(parts)


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
            reply="Hey. What's up — anything on fire, or just looking around?",
            intent_id="global.greeting",
        )

    # Intent: global.status_check — "any fires?", "how's it look?", "how are we doing?"
    # Runs open-case count + top families and gives a quick snapshot.
    if _GLOBAL_STATUS_CHECK_PATTERNS.match(s):
        count_args: Dict[str, Any] = {"status": "open"}
        count_res = run_global_tool(policy=policy, tool="cases.count", args=count_args)
        count_ev = ChatToolEvent(
            tool="cases.count",
            args=count_args,
            ok=bool(count_res.ok),
            result=count_res.result,
            error=count_res.error,
        )
        count = (count_res.result or {}).get("count") if isinstance(count_res.result, dict) else None

        if not count_res.ok or count is None:
            return IntentResult(
                handled=True,
                reply="DB isn't responding right now. Give it a moment and try again.",
                tool_events=[count_ev],
                intent_id="global.status_check",
            )

        if count == 0:
            return IntentResult(
                handled=True,
                reply="All clear. Nothing open right now. Enjoy it.",
                tool_events=[count_ev],
                intent_id="global.status_check",
            )

        # Grab top families for context
        top_args: Dict[str, Any] = {"by": "family", "status": "open", "limit": 5}
        top_res = run_global_tool(policy=policy, tool="cases.top", args=top_args)
        top_ev = ChatToolEvent(
            tool="cases.top",
            args=top_args,
            ok=bool(top_res.ok),
            result=top_res.result,
            error=top_res.error,
        )
        items = (top_res.result or {}).get("items") if isinstance(top_res.result, dict) else []
        family_parts = [
            f"{it.get('key')} ({it.get('count')})" for it in (items or [])[:5] if isinstance(it, dict) and it.get("key")
        ]
        family_line = ", ".join(family_parts) if family_parts else "mixed"
        reply = f"{count} open right now. Biggest offenders: {family_line}. Want me to dig into any of these?"
        return IntentResult(
            handled=True,
            reply=reply,
            tool_events=[count_ev, top_ev],
            intent_id="global.status_check",
        )

    # Intent: global.status_breakdown — "how many stale/snoozed/firing/resolved?"
    # Must be checked BEFORE the generic cases.count handler.
    if ("how many" in s or "count" in s) and _EFFECTIVE_STATUS_RE.search(s):
        m_eff = _EFFECTIVE_STATUS_RE.search(s)
        asked_status = m_eff.group(1).lower().rstrip("e") if m_eff else None
        # Normalise "snooze" → "snoozed", "resolve" → "resolved"
        if asked_status and not asked_status.endswith("d"):
            asked_status = asked_status + "d"

        bd_res = run_global_tool(policy=policy, tool="cases.status_breakdown", args={})
        bd_ev = ChatToolEvent(
            tool="cases.status_breakdown",
            args={},
            ok=bool(bd_res.ok),
            result=bd_res.result,
            error=bd_res.error,
        )
        if not bd_res.ok:
            return IntentResult(
                handled=True,
                reply="DB isn't responding right now. Give it a moment and try again.",
                tool_events=[bd_ev],
                intent_id="global.status_breakdown",
            )

        breakdown = (bd_res.result or {}).get("breakdown", {}) if isinstance(bd_res.result, dict) else {}
        if asked_status and asked_status in breakdown:
            n = breakdown[asked_status]
            reply = f"**{n}** {asked_status} right now."
        else:
            firing = breakdown.get("firing", 0)
            snoozed = breakdown.get("snoozed", 0)
            stale = breakdown.get("stale", 0)
            resolved = breakdown.get("resolved", 0)
            reply = (
                f"Current inbox: **{firing}** firing, **{stale}** stale, "
                f"**{snoozed}** snoozed, **{resolved}** resolved."
            )
        return IntentResult(
            handled=True,
            reply=reply,
            tool_events=[bd_ev],
            intent_id="global.status_breakdown",
        )

    # Intent: global.recent — "show recent cases", "what just came in", "latest alerts"
    if _RECENT_CASES_RE.search(s):
        since_hours = None
        days = _parse_days_window(s)
        if days:
            since_hours = int(days) * 24
        recent_args: Dict[str, Any] = {"limit": 10, "status": "open"}
        if since_hours:
            recent_args["since_hours"] = since_hours
        recent_res = run_global_tool(policy=policy, tool="cases.recent", args=recent_args)
        recent_ev = ChatToolEvent(
            tool="cases.recent",
            args=recent_args,
            ok=bool(recent_res.ok),
            result=recent_res.result,
            error=recent_res.error,
        )
        if not recent_res.ok:
            return IntentResult(
                handled=True,
                reply="DB isn't responding right now. Give it a moment and try again.",
                tool_events=[recent_ev],
                intent_id="global.recent",
            )
        cases = (recent_res.result or {}).get("cases", []) if isinstance(recent_res.result, dict) else []
        if not cases:
            return IntentResult(
                handled=True,
                reply="Nothing open right now. Inbox is clear.",
                tool_events=[recent_ev],
                intent_id="global.recent",
            )
        lines = []
        for c in cases[:10]:
            if not isinstance(c, dict):
                continue
            label = c.get("alertname") or c.get("service") or c.get("case_id", "")[:8]
            one_liner = c.get("one_liner")
            sev = c.get("severity")
            parts = [f"- **{label}**"]
            if sev:
                parts.append(f"({sev})")
            if one_liner:
                parts.append(f"— {one_liner}")
            lines.append(" ".join(parts))
        reply = f"Here are the {len(cases)} most recent open cases:\n" + "\n".join(lines)
        return IntentResult(
            handled=True,
            reply=reply,
            tool_events=[recent_ev],
            intent_id="global.recent",
        )

    # Intent: global.by_severity — "how many critical", "any high severity?"
    if _SEVERITY_RE.search(s) and ("how many" in s or "any" in s or "count" in s or "critical" in s):
        sev_args: Dict[str, Any] = {"status": "open"}
        days = _parse_days_window(s)
        if days:
            sev_args["since_hours"] = int(days) * 24
        sev_res = run_global_tool(policy=policy, tool="cases.by_severity", args=sev_args)
        sev_ev = ChatToolEvent(
            tool="cases.by_severity",
            args=sev_args,
            ok=bool(sev_res.ok),
            result=sev_res.result,
            error=sev_res.error,
        )
        if not sev_res.ok:
            return IntentResult(
                handled=True,
                reply="DB isn't responding right now. Give it a moment and try again.",
                tool_events=[sev_ev],
                intent_id="global.by_severity",
            )
        bd = (sev_res.result or {}).get("breakdown", {}) if isinstance(sev_res.result, dict) else {}
        critical = bd.get("critical", 0)
        warning = bd.get("warning", 0)
        info = bd.get("info", 0)
        total = (sev_res.result or {}).get("total", 0)
        if total == 0:
            reply = "No open cases right now."
        else:
            reply = f"Open cases by severity: **{critical}** critical, **{warning}** warning, **{info}** info."
        return IntentResult(
            handled=True,
            reply=reply,
            tool_events=[sev_ev],
            intent_id="global.by_severity",
        )

    # Intent: global.trending — "what's trending", "is it getting worse?"
    if _TRENDING_RE.search(s):
        trend_args: Dict[str, Any] = {"by": "family", "window_hours": 24}
        trend_res = run_global_tool(policy=policy, tool="cases.trending", args=trend_args)
        trend_ev = ChatToolEvent(
            tool="cases.trending",
            args=trend_args,
            ok=bool(trend_res.ok),
            result=trend_res.result,
            error=trend_res.error,
        )
        if not trend_res.ok:
            return IntentResult(
                handled=True,
                reply="DB isn't responding right now. Give it a moment and try again.",
                tool_events=[trend_ev],
                intent_id="global.trending",
            )
        items = (trend_res.result or {}).get("items", []) if isinstance(trend_res.result, dict) else []
        if not items:
            return IntentResult(
                handled=True,
                reply="No trend data — looks quiet.",
                tool_events=[trend_ev],
                intent_id="global.trending",
            )
        rising = [it for it in items if isinstance(it, dict) and it.get("delta", 0) > 0]
        falling = [it for it in items if isinstance(it, dict) and it.get("delta", 0) < 0]
        lines = []
        for it in items[:8]:
            if not isinstance(it, dict):
                continue
            key = it.get("key", "unknown")
            cur = it.get("current", 0)
            delta = it.get("delta", 0)
            arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
            lines.append(f"- **{key}**: {cur} runs {arrow} ({delta:+d} vs previous 24h)")
        summary = ""
        if rising:
            summary = f" {len(rising)} family(ies) increasing."
        if falling:
            summary += f" {len(falling)} decreasing."
        reply = f"Trends over the last 24h:{summary}\n" + "\n".join(lines)
        return IntentResult(
            handled=True,
            reply=reply,
            tool_events=[trend_ev],
            intent_id="global.trending",
        )

    # Intent: cases.count — only fire when the question is clearly about case counts
    if ("how many" in s or s.startswith("count ") or " count " in s) and (
        "case" in s
        or "alert" in s
        or "incident" in s
        or "open" in s
        or "closed" in s
        or _infer_family(s) is not None
        or _infer_team(s) is not None
        or _infer_classification(s) is not None
    ):
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
                reply="DB isn't responding right now. Give it a moment and try again.",
                tool_events=[ev],
                intent_id="global.cases_count",
            )
        count = (res.result or {}).get("count") if isinstance(res.result, dict) else None
        # Build a descriptive reply using the applied filters
        status_val = str(args.get("status", "all"))
        filter_parts = []
        if fam:
            filter_parts.append(f"family={fam}")
        if team:
            filter_parts.append(f"team={team}")
        if cls:
            filter_parts.append(f"classification={cls}")
        if days:
            filter_parts.append(f"last {days}d")
        filter_str = " · ".join(filter_parts) if filter_parts else status_val
        return IntentResult(
            handled=True,
            reply=f"**{count}** case(s) matching — {filter_str}.",
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
                reply="DB isn't responding right now. Give it a moment and try again.",
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
            reply="Teams by case count (not a leaderboard you want to top):\n" + ("\n".join(lines) if lines else "—"),
            tool_events=[ev],
            intent_id="global.cases_top_team",
        )

    if "top component" in s or "which component" in s or "affected component" in s:
        args = {"by": "component", "status": "all", "limit": 8}
        res = run_global_tool(policy=policy, tool="cases.top", args=args)
        ev = ChatToolEvent(tool="cases.top", args=args, ok=bool(res.ok), result=res.result, error=res.error)
        if not res.ok:
            return IntentResult(
                handled=True,
                reply="DB isn't responding right now. Give it a moment and try again.",
                tool_events=[ev],
                intent_id="global.cases_top_component",
            )
        items = (res.result or {}).get("items") if isinstance(res.result, dict) else []
        lines = []
        for it in (items or [])[:8]:
            if isinstance(it, dict):
                lines.append(f"- {it.get('key')}: {it.get('count')}")
        return IntentResult(
            handled=True,
            reply="Components by case count:\n" + ("\n".join(lines) if lines else "—"),
            tool_events=[ev],
            intent_id="global.cases_top_component",
        )

    return IntentResult(handled=False)


def try_handle_case_intents(*, analysis_json: Dict[str, Any], user_message: str) -> IntentResult:
    """
    Deterministic intents for CASE chat. These should be rare and high-signal.
    """
    s = _norm(user_message)
    if not s:
        return IntentResult(handled=False)

    # Intent: case.greeting — warm reply with verdict context if available, zero tools
    if _GREETING_PATTERNS.match(s):
        tgt = analysis_json.get("target") if isinstance(analysis_json.get("target"), dict) else {}
        target_name = tgt.get("name") or tgt.get("service") or "this case"
        a = analysis_json.get("analysis") if isinstance(analysis_json.get("analysis"), dict) else {}
        verdict = a.get("verdict") if isinstance(a.get("verdict"), dict) else {}
        hyps = a.get("hypotheses") if isinstance(a.get("hypotheses"), list) else []
        verdict_label = verdict.get("label", "")
        top_hyp = hyps[0].get("title") if hyps and isinstance(hyps[0], dict) else None
        if verdict_label:
            reply = f"Hey. **{target_name}** — {verdict_label.lower()}."
            if top_hyp:
                reply += f" Leading suspect: {top_hyp}."
            reply += " What do you want to look at?"
        else:
            reply = f"Hey. What do you want to know about **{target_name}**?"
        return IntentResult(
            handled=True,
            reply=reply,
            intent_id="case.greeting",
        )

    # Intent: case.token_usage — answer token/cost questions from SSOT, zero tools.
    # If keywords match but no data in SSOT, reply directly instead of wasting an LLM call.
    if _is_token_cost_question(s):
        reply = _build_token_usage_reply(analysis_json)
        return IntentResult(
            handled=True,
            reply=reply or "Token and cost information is not available for this investigation.",
            intent_id="case.token_usage",
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
                reply="No Postgres configured — can’t pull historical counts on that.",
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
                    r.family,
                    NULLIF(r.analysis_json #>> '{{analysis,features,k8s,oom_killed}}', '') as oom_flag,
                    r.service as svc,
                    r.namespace as ns,
                    r.cluster as cl
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
                f"Last {days} days: **{runs_count}** run(s) across **{cases_count}** case(s) of `{family}` on {svc_label}."
                " (This is from the case DB — not pod restart counters.)"
            ),
            tool_events=[],
            intent_id="case.family_db_count",
        )

    return IntentResult(handled=False)
