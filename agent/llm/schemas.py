from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _clamp_str(s: Any, *, max_chars: int) -> str:
    txt = "" if s is None else str(s)
    txt = txt.strip()
    if max_chars > 0 and len(txt) > max_chars:
        return txt[: max_chars - 1] + "…"
    return txt


def _clamp_list(xs: Any, *, max_items: int) -> list:
    if not isinstance(xs, list):
        return []
    if max_items > 0 and len(xs) > max_items:
        return xs[:max_items]
    return xs


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tool: str = Field(default="")
    args: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("tool", mode="before")
    @classmethod
    def _tool_trim(cls, v: Any) -> str:
        return _clamp_str(v, max_chars=120)

    @field_validator("args", mode="before")
    @classmethod
    def _args_obj(cls, v: Any) -> Dict[str, Any]:
        return v if isinstance(v, dict) else {}


class ToolPlanMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")

    warnings: List[str] = Field(default_factory=list)

    @field_validator("warnings", mode="before")
    @classmethod
    def _warnings_cap(cls, v: Any) -> List[str]:
        out: List[str] = []
        for x in _clamp_list(v, max_items=6):
            s = _clamp_str(x, max_chars=140)
            if s:
                out.append(s)
        return out


class ToolPlanResponse(BaseModel):
    """
    Versioned envelope for tool-planning LLM calls.
    """

    model_config = ConfigDict(extra="ignore")

    schema_version: Literal["tarka.tool_plan.v1"] = "tarka.tool_plan.v1"
    reply: str = ""
    tool_calls: List[ToolCall] = Field(default_factory=list)
    meta: Optional[ToolPlanMeta] = None

    @field_validator("reply", mode="before")
    @classmethod
    def _reply_trim(cls, v: Any) -> str:
        # Keep user-facing replies compact.
        return _clamp_str(v, max_chars=600)

    @field_validator("tool_calls", mode="before")
    @classmethod
    def _tool_calls_cap(cls, v: Any) -> List[ToolCall]:
        # Executor side will cap as well, but keep the model's output bounded.
        items = _clamp_list(v, max_items=3)
        out: List[ToolCall] = []
        for x in items:
            try:
                out.append(ToolCall.model_validate(x))
            except Exception:
                continue
        return out


class RCASynthesisMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")

    notes: List[str] = Field(default_factory=list)

    @field_validator("notes", mode="before")
    @classmethod
    def _notes_cap(cls, v: Any) -> List[str]:
        out: List[str] = []
        for x in _clamp_list(v, max_items=6):
            s = _clamp_str(x, max_chars=160)
            if s:
                out.append(s)
        return out


class RCASynthesisResponse(BaseModel):
    """
    Versioned envelope for RCA synthesis calls.
    """

    model_config = ConfigDict(extra="ignore")

    schema_version: Literal["tarka.rca.v1"] = "tarka.rca.v1"
    status: Literal["ok", "unknown", "blocked"] = "unknown"
    summary: str = ""
    root_cause: str = ""
    confidence_0_1: float = 0.0
    evidence: List[str] = Field(default_factory=list)
    remediation: List[str] = Field(default_factory=list)
    unknowns: List[str] = Field(default_factory=list)
    meta: Optional[RCASynthesisMeta] = None

    @field_validator("summary", mode="before")
    @classmethod
    def _summary_trim(cls, v: Any) -> str:
        return _clamp_str(v, max_chars=240)

    @field_validator("root_cause", mode="before")
    @classmethod
    def _rc_trim(cls, v: Any) -> str:
        return _clamp_str(v, max_chars=240)

    @field_validator("confidence_0_1", mode="before")
    @classmethod
    def _conf_float(cls, v: Any) -> float:
        try:
            x = float(v)
        except Exception:
            x = 0.0
        if x != x:  # NaN
            x = 0.0
        return max(0.0, min(x, 1.0))

    @field_validator("evidence", mode="before")
    @classmethod
    def _evidence_cap(cls, v: Any) -> List[str]:
        out: List[str] = []
        for x in _clamp_list(v, max_items=8):
            s = _clamp_str(x, max_chars=160)
            if s:
                out.append(s)
        return out

    @field_validator("remediation", mode="before")
    @classmethod
    def _remediation_cap(cls, v: Any) -> List[str]:
        out: List[str] = []
        for x in _clamp_list(v, max_items=10):
            s = _clamp_str(x, max_chars=160)
            if s:
                out.append(s)
        return out

    @field_validator("unknowns", mode="before")
    @classmethod
    def _unknowns_cap(cls, v: Any) -> List[str]:
        out: List[str] = []
        for x in _clamp_list(v, max_items=8):
            s = _clamp_str(x, max_chars=160)
            if s:
                out.append(s)
        return out


class EnrichmentMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")

    warnings: List[str] = Field(default_factory=list)

    @field_validator("warnings", mode="before")
    @classmethod
    def _warnings_cap(cls, v: Any) -> List[str]:
        out: List[str] = []
        for x in _clamp_list(v, max_items=6):
            s = _clamp_str(x, max_chars=140)
            if s:
                out.append(s)
        return out


class EnrichmentResponse(BaseModel):
    """
    Versioned envelope for report-time enrichment.
    """

    model_config = ConfigDict(extra="ignore")

    schema_version: Literal["tarka.enrich.v1"] = "tarka.enrich.v1"
    summary: str = ""
    likely_root_cause: str = ""
    confidence: float = 0.0
    evidence: List[str] = Field(default_factory=list)
    next_steps: List[str] = Field(default_factory=list)
    unknowns: List[str] = Field(default_factory=list)
    meta: Optional[EnrichmentMeta] = None

    @field_validator("summary", mode="before")
    @classmethod
    def _summary_trim(cls, v: Any) -> str:
        return _clamp_str(v, max_chars=200)

    @field_validator("likely_root_cause", mode="before")
    @classmethod
    def _lrc_trim(cls, v: Any) -> str:
        return _clamp_str(v, max_chars=200)

    @field_validator("confidence", mode="before")
    @classmethod
    def _conf_float(cls, v: Any) -> float:
        try:
            x = float(v)
        except Exception:
            x = 0.0
        if x != x:  # NaN
            x = 0.0
        return max(0.0, min(x, 1.0))

    @field_validator("evidence", mode="before")
    @classmethod
    def _evidence_cap(cls, v: Any) -> List[str]:
        out: List[str] = []
        for x in _clamp_list(v, max_items=5):
            s = _clamp_str(x, max_chars=140)
            if s:
                out.append(s)
        return out

    @field_validator("next_steps", mode="before")
    @classmethod
    def _steps_cap(cls, v: Any) -> List[str]:
        out: List[str] = []
        for x in _clamp_list(v, max_items=5):
            s = _clamp_str(x, max_chars=140)
            if s:
                out.append(s)
        return out

    @field_validator("unknowns", mode="before")
    @classmethod
    def _unknowns_cap(cls, v: Any) -> List[str]:
        out: List[str] = []
        for x in _clamp_list(v, max_items=5):
            s = _clamp_str(x, max_chars=140)
            if s:
                out.append(s)
        return out
