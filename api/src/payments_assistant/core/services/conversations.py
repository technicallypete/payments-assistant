"""Conversations and messages for both channels (spec §5.1). Transcripts are append-only."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from payments_assistant.core.models import Conversation, Message

TITLE_MAX = 60
HISTORY_LIMIT = 40  # most recent user/assistant messages sent to the model


async def create_owner_conversation(
    session: AsyncSession, *, owner_id: UUID, now: datetime, title: str | None = None
) -> Conversation:
    conv = Conversation(
        channel="owner_web", owner_id=owner_id, title=title, created_at=now, last_message_at=now
    )
    session.add(conv)
    await session.flush()
    return conv


async def list_owner_conversations(
    session: AsyncSession, *, owner_id: UUID, limit: int = 50
) -> Sequence[Conversation]:
    return (
        (
            await session.execute(
                select(Conversation)
                .where(Conversation.owner_id == owner_id, Conversation.channel == "owner_web")
                .order_by(Conversation.last_message_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


async def get_owner_conversation(
    session: AsyncSession, *, owner_id: UUID, conversation_id: UUID
) -> Conversation | None:
    conv = await session.get(Conversation, conversation_id)
    if conv is None or conv.owner_id != owner_id or conv.channel != "owner_web":
        return None
    return conv


async def list_messages(session: AsyncSession, conversation_id: UUID) -> Sequence[Message]:
    return (
        (
            await session.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.seq)
            )
        )
        .scalars()
        .all()
    )


async def append_message(
    session: AsyncSession,
    conv: Conversation,
    *,
    role: str,
    content: str,
    now: datetime,
    status: str = "complete",
    message_id: UUID | None = None,
    tool_name: str | None = None,
    tool_payload: dict[str, Any] | None = None,
    llm_model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> Message:
    msg = Message(
        id=message_id or uuid4(),
        conversation_id=conv.id,
        customer_account_id=conv.customer_account_id,
        role=role,
        content=content,
        status=status,
        tool_name=tool_name,
        tool_payload=tool_payload,
        llm_model=llm_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        created_at=now,
    )
    session.add(msg)
    conv.last_message_at = now
    if role == "user" and not conv.title:
        conv.title = content.strip().replace("\n", " ")[:TITLE_MAX] or None
    await session.flush()
    return msg


def history_for_model(messages: Sequence[Message]) -> list[BaseMessage]:
    """User/assistant text only. Tool rows are audit trail, not prompt: the model re-runs lookups,
    so stale ids or figures from earlier turns never get reused as if current."""
    out: list[BaseMessage] = []
    for m in messages:
        if m.role == "user":
            out.append(HumanMessage(m.content))
        elif m.role == "assistant" and m.content:
            out.append(AIMessage(m.content))
    return out[-HISTORY_LIMIT:]
