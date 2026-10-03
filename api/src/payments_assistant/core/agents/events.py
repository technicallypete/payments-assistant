"""Events the agent loop yields. The HTTP layer serializes them as SSE (spec §6.1); the Telegram
adapter just collects the final text. Pydantic so the TS client types can be generated."""

from typing import Any, Literal

from pydantic import BaseModel


class MessageStart(BaseModel):
    type: Literal["message_start"] = "message_start"
    model: str


class Token(BaseModel):
    type: Literal["token"] = "token"
    delta: str


class ToolStart(BaseModel):
    type: Literal["tool_start"] = "tool_start"
    tool: str
    label: str


class ToolEnd(BaseModel):
    type: Literal["tool_end"] = "tool_end"
    tool: str
    ok: bool


class ActionProposed(BaseModel):
    type: Literal["action_proposed"] = "action_proposed"
    action_id: str
    action_type: str
    preview: str
    expires_at: str


class ToolRecord(BaseModel):
    """What a tool call did, for persistence as a `tool` message."""

    tool: str
    args: dict[str, Any]
    result: dict[str, Any] | None
    error: str | None


class MessageEnd(BaseModel):
    type: Literal["message_end"] = "message_end"
    text: str
    input_tokens: int
    output_tokens: int
    tool_records: list[ToolRecord]
    truncated: bool = False  # hit the tool-hop limit


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    code: str
    message: str


AgentEvent = MessageStart | Token | ToolStart | ToolEnd | ActionProposed | MessageEnd | ErrorEvent
