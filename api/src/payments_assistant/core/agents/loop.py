"""Generic streaming tool loop over the tool registry.

Deliberately hand-rolled rather than a framework agent: the confirm gate and customer scoping stay
explicit, and every event the UI needs is emitted at a known point.
"""

import json
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from pydantic import ValidationError

from payments_assistant.core.agents.events import (
    ActionProposed,
    AgentEvent,
    ErrorEvent,
    MessageEnd,
    MessageStart,
    Token,
    ToolEnd,
    ToolRecord,
    ToolStart,
)
from payments_assistant.core.tools.registry import ToolContext, ToolError, ToolSpec

logger = logging.getLogger(__name__)

# Sent (not persisted) when a model ends its turn with no text after using tools. Seen with
# reasoning models via OpenRouter: the answer sometimes stays in the reasoning channel.
EMPTY_REPLY_NUDGE = "Please reply to me now, using the tool results above."

HOP_LIMIT_NOTE = (
    "\n\n(I stopped after several lookups without finishing. Could you rephrase or narrow it down?)"
)


def _text(content: Any) -> str:
    """Chunk content is a str, or a list of content blocks for some providers."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") if isinstance(b, dict) else str(b)
            for b in content
            if not isinstance(b, dict) or b.get("type") in (None, "text")
        )
    return ""


async def run_agent(
    model: BaseChatModel,
    *,
    system_prompt: str,
    history: Sequence[BaseMessage],
    tools: Sequence[ToolSpec],
    ctx: ToolContext,
    max_hops: int,
    model_name: str,
) -> AsyncIterator[AgentEvent]:
    by_name = {t.name: t for t in tools}
    bound = model.bind_tools([t.json_schema() for t in tools]) if tools else model
    messages: list[BaseMessage] = [SystemMessage(system_prompt), *history]
    text_parts: list[str] = []
    records: list[ToolRecord] = []
    usage_in = usage_out = 0
    nudged = False

    yield MessageStart(model=model_name)
    try:
        for _hop in range(max_hops):
            acc: AIMessageChunk | None = None
            async for chunk in bound.astream(messages):
                acc = chunk if acc is None else acc + chunk
                delta = _text(chunk.content)
                if delta:
                    text_parts.append(delta)
                    yield Token(delta=delta)
            if acc is None:
                break
            if acc.usage_metadata:
                usage_in += acc.usage_metadata.get("input_tokens", 0)
                usage_out += acc.usage_metadata.get("output_tokens", 0)
            # Send back only text + tool calls. Provider extras (e.g. streamed reasoning_details)
            # don't survive chunk concatenation intact and confuse the next request.
            messages.append(AIMessage(content=acc.content, tool_calls=acc.tool_calls))
            if not acc.tool_calls:
                turn_text = _text(acc.content).strip()
                if not turn_text and records and not nudged:
                    nudged = True
                    messages.append(HumanMessage(EMPTY_REPLY_NUDGE))
                    continue
                break

            for call in acc.tool_calls:
                spec = by_name.get(call["name"])
                label = spec.label if spec else call["name"]
                yield ToolStart(tool=call["name"], label=label)
                content, record = await _run_tool(spec, call, ctx)
                records.append(record)
                messages.append(ToolMessage(content=content, tool_call_id=call["id"] or ""))
                yield ToolEnd(tool=call["name"], ok=record.error is None)
                # Surface any proposals created by this call as Confirm cards.
                while ctx.proposals:
                    p = ctx.proposals.pop(0)
                    yield ActionProposed(
                        action_id=str(p.action_id),
                        action_type=p.action_type,
                        preview=p.preview,
                        expires_at=p.expires_at.isoformat(),
                    )
        else:
            text_parts.append(HOP_LIMIT_NOTE)
            yield Token(delta=HOP_LIMIT_NOTE)
            yield MessageEnd(
                text="".join(text_parts),
                input_tokens=usage_in,
                output_tokens=usage_out,
                tool_records=records,
                truncated=True,
            )
            return
    except Exception as exc:  # model/provider failure: report, keep partial text
        logger.exception("agent turn failed")
        yield ErrorEvent(code="llm_error", message=f"The assistant hit an error: {exc}"[:300])
        yield MessageEnd(
            text="".join(text_parts),
            input_tokens=usage_in,
            output_tokens=usage_out,
            tool_records=records,
        )
        return

    yield MessageEnd(
        text="".join(text_parts),
        input_tokens=usage_in,
        output_tokens=usage_out,
        tool_records=records,
    )


async def _run_tool(
    spec: ToolSpec | None, call: dict[str, Any], ctx: ToolContext
) -> tuple[str, ToolRecord]:
    args = call.get("args") or {}
    if spec is None:
        msg = f"Unknown tool {call['name']!r}."
        return json.dumps({"error": msg}), ToolRecord(
            tool=call["name"], args=args, result=None, error=msg
        )
    try:
        result = await spec.run(ctx, args)
    except ValidationError as exc:
        msg = f"Invalid arguments: {exc.errors(include_url=False, include_context=False)}"
    except ToolError as exc:
        msg = str(exc)
    except PermissionError:
        msg = "That isn't available here."
    except Exception as exc:  # unexpected: log it, tell the model something went wrong
        logger.exception("tool %s failed", spec.name)
        msg = f"The {spec.name} lookup failed: {type(exc).__name__}."
    else:
        payload = result.model_dump(mode="json")
        return json.dumps(payload), ToolRecord(
            tool=spec.name, args=args, result=payload, error=None
        )
    return json.dumps({"error": msg}), ToolRecord(tool=spec.name, args=args, result=None, error=msg)
