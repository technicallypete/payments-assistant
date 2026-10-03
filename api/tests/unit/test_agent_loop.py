"""The streaming tool loop, driven by a scripted model (no network, no DB)."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from pydantic import BaseModel, Field

from payments_assistant.core.agents.events import (
    ActionProposed,
    ErrorEvent,
    MessageEnd,
    MessageStart,
    Token,
    ToolEnd,
    ToolStart,
)
from payments_assistant.core.agents.loop import HOP_LIMIT_NOTE, run_agent
from payments_assistant.core.tools.owner import ActionProposal
from payments_assistant.core.tools.registry import ToolContext, ToolError, ToolSpec
from tests.fake_llm import ScriptedChatModel, reply


class EchoIn(BaseModel):
    word: str = Field(min_length=1)


class EchoOut(BaseModel):
    echoed: str


async def _echo(ctx, args: EchoIn) -> EchoOut:
    if args.word == "boom":
        raise ToolError("nothing called boom")
    if args.word == "crash":
        raise RuntimeError("db down")
    return EchoOut(echoed=args.word.upper())


async def _propose(ctx, args: EchoIn) -> ActionProposal:
    p = ActionProposal(
        action_id=str(uuid4()),
        action_type="refund",
        preview=f"Refund {args.word}",
        expires_at=datetime(2026, 10, 3, tzinfo=UTC),
    )
    ctx.proposals.append(p)
    return p


ECHO = ToolSpec(
    "echo", "Echo a word back in caps.", "owner", False, EchoIn, EchoOut, _echo, "Echoing"
)
PROPOSE = ToolSpec(
    "propose_x", "Propose a thing.", "owner", True, EchoIn, ActionProposal, _propose, "Proposing"
)


@pytest.fixture
def ctx(settings):
    return ToolContext(session=None, settings=settings, now=datetime(2026, 10, 3, tzinfo=UTC))  # type: ignore[arg-type]


async def _run(model, ctx, tools=(ECHO,), max_hops=6):
    return [
        e
        async for e in run_agent(
            model,
            system_prompt="sys",
            history=[HumanMessage("hi")],
            tools=list(tools),
            ctx=ctx,
            max_hops=max_hops,
            model_name="scripted",
        )
    ]


def _types(events):
    return [e.type for e in events]


async def test_plain_answer_streams_tokens(ctx):
    model = ScriptedChatModel(script=[reply("Hello there friend")])
    events = await _run(model, ctx)
    assert isinstance(events[0], MessageStart)
    assert "".join(e.delta for e in events if isinstance(e, Token)) == "Hello there friend"
    assert len([e for e in events if isinstance(e, Token)]) == 3
    end = events[-1]
    assert isinstance(end, MessageEnd) and end.text == "Hello there friend"
    assert end.input_tokens == 10 and end.output_tokens == 5


async def test_tool_call_round_trip_and_event_order(ctx):
    model = ScriptedChatModel(
        script=[reply("Checking.", ("echo", {"word": "hi"})), reply("Done: HI")]
    )
    events = await _run(model, ctx)
    assert _types(events) == [
        "message_start",
        "token",
        "tool_start",
        "tool_end",
        "token",
        "token",
        "message_end",
    ]
    assert events[2] == ToolStart(tool="echo", label="Echoing")
    assert events[3] == ToolEnd(tool="echo", ok=True)
    # The model's second turn saw the tool result.
    tool_msg = model.seen[1][-1]
    assert isinstance(tool_msg, ToolMessage)
    assert json.loads(tool_msg.content) == {"echoed": "HI"}
    end = events[-1]
    assert end.text == "Checking.Done: HI"
    assert end.tool_records[0].result == {"echoed": "HI"}
    assert end.input_tokens == 20  # summed across both model calls


async def test_bound_tools_are_the_given_specs(ctx):
    model = ScriptedChatModel(script=[reply("ok")])
    await _run(model, ctx, tools=(ECHO, PROPOSE))
    assert [t["function"]["name"] for t in model.bound_tools] == ["echo", "propose_x"]


@pytest.mark.parametrize(
    "args,expect",
    [
        ({"word": ""}, "Invalid arguments"),
        ({}, "Invalid arguments"),
        ({"word": "boom"}, "nothing called boom"),
        ({"word": "crash"}, "lookup failed: RuntimeError"),
    ],
)
async def test_tool_errors_go_back_to_the_model_not_the_user(ctx, args, expect):
    model = ScriptedChatModel(script=[reply("", ("echo", args)), reply("Sorry about that.")])
    events = await _run(model, ctx)
    assert ToolEnd(tool="echo", ok=False) in events
    tool_msg = model.seen[1][-1]
    assert expect in json.loads(tool_msg.content)["error"]
    assert "db down" not in tool_msg.content  # internals never leak
    assert events[-1].tool_records[0].error


async def test_unknown_tool_is_reported_to_model(ctx):
    model = ScriptedChatModel(
        script=[reply("", ("delete_everything", {})), reply("Can't do that.")]
    )
    events = await _run(model, ctx)
    assert "Unknown tool" in json.loads(model.seen[1][-1].content)["error"]
    assert events[-1].text == "Can't do that."


async def test_proposals_surface_as_events(ctx):
    model = ScriptedChatModel(script=[reply("", ("propose_x", {"word": "$5"})), reply("Confirm?")])
    events = await _run(model, ctx, tools=(PROPOSE,))
    proposed = [e for e in events if isinstance(e, ActionProposed)]
    assert len(proposed) == 1 and proposed[0].preview == "Refund $5"
    assert _types(events).index("action_proposed") == _types(events).index("tool_end") + 1
    assert ctx.proposals == []


async def test_hop_limit_stops_and_says_so(ctx):
    model = ScriptedChatModel(script=[reply("", ("echo", {"word": "x"}))] * 3)
    events = await _run(model, ctx, max_hops=3)
    end = events[-1]
    assert isinstance(end, MessageEnd) and end.truncated
    assert end.text.endswith(HOP_LIMIT_NOTE)
    assert len(model.seen) == 3


async def test_provider_error_yields_error_then_end_with_partial_text(ctx):
    model = ScriptedChatModel(script=[reply("Partial", ("echo", {"word": "a"}))], fail_on_turn=1)
    events = await _run(model, ctx)
    assert isinstance(events[-2], ErrorEvent) and events[-2].code == "llm_error"
    assert isinstance(events[-1], MessageEnd) and events[-1].text == "Partial"


async def test_empty_final_reply_after_tools_gets_one_nudge(ctx):
    model = ScriptedChatModel(
        script=[reply("", ("echo", {"word": "hi"})), reply(""), reply("Here you go: HI")]
    )
    events = await _run(model, ctx)
    assert events[-1].text == "Here you go: HI"
    nudge = model.seen[2][-1]
    assert isinstance(nudge, HumanMessage) and "reply" in nudge.content


async def test_nudge_happens_at_most_once(ctx):
    model = ScriptedChatModel(script=[reply("", ("echo", {"word": "hi"})), reply(""), reply("")])
    events = await _run(model, ctx)
    assert events[-1].text == ""
    assert len(model.seen) == 3


async def test_history_sent_back_is_clean(ctx):
    model = ScriptedChatModel(script=[reply("", ("echo", {"word": "hi"})), reply("ok")])
    await _run(model, ctx)
    ai = model.seen[1][-2]
    assert type(ai).__name__ == "AIMessage" and ai.additional_kwargs == {}
