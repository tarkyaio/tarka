"""Optional, additive LLM enrichment for Investigation.

Key requirements:
- Off by default (local runs shouldn't need any LLM).
- Lazy import provider SDK only when enabled.
- Never raises; never blocks report generation.
"""

from __future__ import annotations

import json
import os

from agent.core.models import Investigation, LLMInsights
from agent.llm.evidence import build_evidence_pack
from agent.llm.schemas import EnrichmentResponse


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "y", "on")


def _status_from_err(err: str) -> str:
    e = (err or "").lower()
    if "rate" in e and "limit" in e:
        return "rate_limited"
    if any(
        x in e
        for x in (
            "missing_adc_credentials",
            "missing_gcp_project",
            "sdk_import_failed",
            "adc_import_failed",
        )
    ):
        return "unavailable"
    return "error"


def maybe_enrich_investigation(investigation: Investigation, *, enabled: bool) -> None:
    """
    Mutates investigation in-place by setting `investigation.analysis.llm` when enabled.
    """
    if not enabled and not _env_bool("LLM_ENABLED", False):
        return

    try:
        evidence = build_evidence_pack(investigation)

        prompt = (
            "You are an SRE assistant.\n"
            "\n"
            "Your approach:\n"
            "- Be autonomous and proactive in your analysis—synthesize all available evidence.\n"
            "- Draw direct conclusions from evidence—don't defer or suggest external investigation.\n"
            "- Be confident when evidence is strong, honest when it's weak.\n"
            "- PRIORITIZE diagnostic hypotheses and parsed errors - they are based on pattern matching from actual logs.\n"
            "\n"
            "Hard constraints (must follow):\n"
            "- Use ONLY the provided EVIDENCE JSON. Do NOT use outside knowledge.\n"
            "- Do NOT make up logs, metrics, events, Kubernetes states, timelines, or root causes.\n"
            "- If a fact is not explicitly supported by EVIDENCE, say it is unknown and put it in `unknowns`.\n"
            '- If EVIDENCE.logs.entries_count == 0 or EVIDENCE.logs.status != "ok":\n'
            "  - Do NOT claim what logs say.\n"
            "  - You may only mention logs availability/status from EVIDENCE.logs.\n"
            "- If EVIDENCE.diagnostics.hypotheses is present, use the top hypothesis as primary root cause direction.\n"
            "- If EVIDENCE.diagnostics.parsed_errors is present, reference specific error patterns in your analysis.\n"
            "- Every assertion should be backed by a concrete evidence bullet (metric/condition/event) in `evidence`.\n"
            "\n"
            "Output format constraints:\n"
            "- Return ONLY valid JSON (no markdown, no code fences).\n"
            "- Keep strings short.\n"
            "- Limit arrays to max 5 items each.\n"
            "- Do NOT include any extra top-level keys.\n"
            "\n"
            "Based ONLY on the provided evidence JSON, produce a JSON object with exactly these keys:\n"
            '- schema_version: "tarka.enrich.v1"\n'
            "- summary: string (<= 200 chars)\n"
            "- likely_root_cause: string (<= 200 chars)\n"
            "- confidence: number between 0 and 1\n"
            "- evidence: array of strings (max 5; each <= 140 chars)\n"
            "- next_steps: array of strings (max 5; each <= 140 chars)\n"
            "- unknowns: array of strings (max 5; each <= 140 chars)\n"
            "- meta: { warnings: [string] } | null\n"
            "Return ONLY valid JSON (no markdown, no code fences).\n\n"
            f"EVIDENCE:\n{json.dumps(evidence, sort_keys=True)}"
        )

        # Lazy import: only when enabled.
        from agent.llm.client import generate_json  # noqa: WPS433

        obj, err = generate_json(prompt, schema=EnrichmentResponse)
        if err:
            investigation.analysis.llm = LLMInsights(
                provider=(os.getenv("LLM_PROVIDER") or "").strip().lower() or "vertexai",
                status=_status_from_err(err),
                error=err,
            )
            return

        investigation.analysis.llm = LLMInsights(
            provider=(os.getenv("LLM_PROVIDER") or "").strip().lower() or "vertexai",
            status="ok",
            model=(os.getenv("LLM_MODEL") or "").strip() or "gemini-2.5-flash",
            output=obj,
        )
    except Exception as e:
        investigation.analysis.llm = LLMInsights(
            provider=(os.getenv("LLM_PROVIDER") or "").strip().lower() or "vertexai",
            status="error",
            error=f"enrich_error:{type(e).__name__}",
        )
