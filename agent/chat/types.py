from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

ChatRole = Literal["user", "assistant", "tool"]
ChatToolOutcome = Literal["ok", "empty", "unavailable", "error", "skipped_duplicate"]


class ChatMessage(BaseModel):
    role: ChatRole
    content: str
    name: Optional[str] = None


class ChatToolCall(BaseModel):
    tool: str
    args: Dict[str, Any] = Field(default_factory=dict)


class ChatToolEvent(BaseModel):
    tool: str
    args: Dict[str, Any] = Field(default_factory=dict)
    ok: bool
    result: Any = None
    error: Optional[str] = None
    # Optional metadata used to reduce repeated tool calls and improve prompting.
    outcome: Optional[ChatToolOutcome] = None
    summary: Optional[str] = None
    key: Optional[str] = None


class ChatRequest(BaseModel):
    run_id: str
    message: str
    history: List[ChatMessage] = Field(default_factory=list)


class ChatResponse(BaseModel):
    reply: str
    tool_events: List[ChatToolEvent] = Field(default_factory=list)
    # Best-effort: return updated analysis fields when rerun tool is used.
    updated_analysis: Optional[Dict[str, Any]] = None
