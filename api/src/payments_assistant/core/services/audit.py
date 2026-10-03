"""Append-only audit trail. Core INSERT, no RETURNING: the bot role may insert but not read."""

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from payments_assistant.core.models import AuditLog


async def record(
    session: AsyncSession,
    *,
    actor_type: str,
    action: str,
    actor_id: str | None = None,
    customer_account_id: UUID | None = None,
    target: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    await session.execute(
        insert(AuditLog).values(
            id=uuid4(),
            actor_type=actor_type,
            actor_id=actor_id,
            customer_account_id=customer_account_id,
            action=action,
            target=target,
            payload=payload,
        )
    )
