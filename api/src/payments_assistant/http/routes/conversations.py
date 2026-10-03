"""Owner chat: conversations and the streaming message endpoint (spec §6.1)."""

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from payments_assistant.core.agents.events import (
    ActionProposed,
    ErrorEvent,
    MessageEnd,
    MessageStart,
    Token,
)
from payments_assistant.core.agents.owner_agent import run_owner_turn
from payments_assistant.core.models import OwnerAction
from payments_assistant.core.services import conversations as convs
from payments_assistant.core.tools import ToolContext
from payments_assistant.http.deps import Owner, Session, State
from payments_assistant.http.routes.actions import ActionOut, action_out
from payments_assistant.http.sse import sse_frame, sse_response
from payments_assistant.http.state import AppState

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/conversations", tags=["conversations"])


class ConversationOut(BaseModel):
    id: UUID
    title: str | None
    created_at: datetime
    last_message_at: datetime


class MessageOut(BaseModel):
    id: UUID
    role: str
    content: str
    status: str
    tool_name: str | None
    created_at: datetime
    # Proposals made during this assistant turn, with their CURRENT status, so a reloaded
    # conversation still shows its cards (pending ones actionable, others as result stamps).
    actions: list[ActionOut] = []


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class CreateConversationIn(BaseModel):
    title: str | None = Field(None, max_length=120)


class SendMessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


def _conv_out(c) -> ConversationOut:
    return ConversationOut(
        id=c.id, title=c.title, created_at=c.created_at, last_message_at=c.last_message_at
    )


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: CreateConversationIn, owner: Owner, session: Session, state: State
) -> ConversationOut:
    conv = await convs.create_owner_conversation(
        session, owner_id=owner.owner_id, now=state.clock(), title=body.title
    )
    return _conv_out(conv)


@router.get("", response_model=list[ConversationOut])
async def list_conversations(owner: Owner, session: Session) -> list[ConversationOut]:
    return [
        _conv_out(c) for c in await convs.list_owner_conversations(session, owner_id=owner.owner_id)
    ]


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: UUID, owner: Owner, session: Session, state: State
) -> ConversationDetail:
    conv = await convs.get_owner_conversation(
        session, owner_id=owner.owner_id, conversation_id=conversation_id
    )
    if conv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.")
    messages = await convs.list_messages(session, conv.id)
    by_message = _actions_by_message(
        messages,
        (
            await session.execute(
                select(OwnerAction)
                .where(OwnerAction.conversation_id == conv.id, OwnerAction.api_key_id.is_(None))
                .order_by(OwnerAction.created_at)
            )
        ).scalars(),
        now=state.clock(),
    )
    return ConversationDetail(
        **_conv_out(conv).model_dump(),
        messages=[
            MessageOut(
                id=m.id,
                role=m.role,
                content=m.content,
                status=m.status,
                tool_name=m.tool_name,
                created_at=m.created_at,
                actions=by_message.get(m.id, []),
            )
            for m in messages
        ],
    )


def _actions_by_message(messages, actions, *, now: datetime) -> dict[UUID, list[ActionOut]]:
    """Attach each proposal to the assistant reply of the turn that created it: the first assistant
    message written at or after the proposal (replies are persisted when the turn ends)."""
    replies = [m for m in messages if m.role == "assistant"]
    out: dict[UUID, list[ActionOut]] = {}
    for a in actions:
        reply = next((m for m in replies if m.created_at >= a.created_at), None)
        if reply is None and replies:
            reply = replies[-1]
        if reply is not None:
            out.setdefault(reply.id, []).append(action_out(a, now=now))
    return out


@router.post("/{conversation_id}/messages")
async def send_message(conversation_id: UUID, body: SendMessageIn, owner: Owner, state: State):
    # 1. Persist the user's message (and read history) BEFORE streaming starts.
    async with state.sessionmaker() as session, session.begin():
        conv = await convs.get_owner_conversation(
            session, owner_id=owner.owner_id, conversation_id=conversation_id
        )
        if conv is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.")
        await convs.append_message(
            session, conv, role="user", content=body.content, now=state.clock()
        )
        history = convs.history_for_model(await convs.list_messages(session, conv.id))

    return sse_response(_stream_turn(state, owner.owner_id, conversation_id, history))


async def _stream_turn(
    state: AppState, owner_id: UUID, conversation_id: UUID, history: list
) -> AsyncIterator[bytes]:
    """2. Stream the agent turn. The assistant message (complete, interrupted, or error) and any
    proposals are committed in `finally`, shielded so a client disconnect can't skip it."""
    message_id = uuid4()
    model_name = state.model_name()
    session = state.sessionmaker()
    await session.begin()
    ctx = ToolContext(
        session=session,
        settings=state.settings,
        now=state.clock(),
        owner_id=owner_id,
        owner_gateway=state.owner_gateway(),
        conversation_id=conversation_id,
    )
    text_parts: list[str] = []
    end: MessageEnd | None = None
    outcome = "interrupted"  # unless we reach the end normally
    try:
        async for event in run_owner_turn(
            state.model("agent"), ctx=ctx, history=history, model_name=model_name
        ):
            if isinstance(event, MessageStart):
                event = event.model_copy(update={"message_id": str(message_id)})
            elif isinstance(event, Token):
                text_parts.append(event.delta)
            elif isinstance(event, ErrorEvent):
                outcome = "error"
            elif isinstance(event, MessageEnd):
                end = event = event.model_copy(update={"message_id": str(message_id)})
            elif isinstance(event, ActionProposed):
                pass
            yield sse_frame(event)
        if outcome != "error":
            outcome = "complete"
    except Exception:
        logger.exception("chat turn failed")
        outcome = "error"
        yield sse_frame(ErrorEvent(code="server_error", message="Something went wrong."))
    finally:
        await asyncio.shield(
            _finish_turn(
                state,
                session,
                conversation_id,
                message_id,
                model_name,
                outcome,
                "".join(text_parts),
                end,
            )
        )


async def _finish_turn(
    state: AppState,
    session,
    conversation_id: UUID,
    message_id: UUID,
    model_name: str,
    outcome: str,
    partial_text: str,
    end: MessageEnd | None,
) -> None:
    try:
        conv = await session.get(convs.Conversation, conversation_id)
        now = state.clock()
        for record in end.tool_records if end else []:
            payload: dict[str, Any] = record.model_dump(mode="json")
            await convs.append_message(
                session,
                conv,
                role="tool",
                content="",
                now=now,
                tool_name=record.tool,
                tool_payload=payload,
            )
        await convs.append_message(
            session,
            conv,
            role="assistant",
            content=end.text if end else partial_text,
            now=now,
            status=outcome,
            message_id=message_id,
            llm_model=model_name,
            input_tokens=end.input_tokens if end else None,
            output_tokens=end.output_tokens if end else None,
        )
        await session.commit()
    except Exception:
        logger.exception("failed to persist chat turn")
        await session.rollback()
    finally:
        await session.close()
