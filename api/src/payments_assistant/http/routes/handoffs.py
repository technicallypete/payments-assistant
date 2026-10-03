"""Handoff queue: payments the bot wouldn't take (≥ threshold) and customers asking for a human."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select

from payments_assistant.core.models import CustomerAccount, Handoff
from payments_assistant.core.money import format_money
from payments_assistant.core.services import audit
from payments_assistant.http.deps import Owner, Session, State

router = APIRouter(prefix="/handoffs", tags=["handoffs"])

StatusFilter = Literal["open", "acknowledged", "resolved", "all"]


class HandoffOut(BaseModel):
    id: UUID
    customer_account_id: UUID
    customer: str
    reason: str
    status: str
    stripe_invoice_id: str | None
    amount: str | None
    summary: str
    created_at: datetime
    resolved_at: datetime | None


def _out(h: Handoff, customer: str) -> HandoffOut:
    return HandoffOut(
        id=h.id,
        customer_account_id=h.customer_account_id,
        customer=customer,
        reason=h.reason,
        status=h.status,
        stripe_invoice_id=h.stripe_invoice_id,
        amount=format_money(h.amount, h.currency) if h.amount is not None and h.currency else None,
        summary=h.summary,
        created_at=h.created_at,
        resolved_at=h.resolved_at,
    )


@router.get("", response_model=list[HandoffOut])
async def list_handoffs(
    owner: Owner,
    session: Session,
    status_: Annotated[StatusFilter, Query(alias="status")] = "open",
) -> list[HandoffOut]:
    stmt = (
        select(Handoff, CustomerAccount.display_name)
        .join(CustomerAccount, CustomerAccount.id == Handoff.customer_account_id)
        .order_by(Handoff.created_at.desc(), Handoff.id)
    )
    if status_ != "all":
        stmt = stmt.where(Handoff.status == status_)
    return [_out(h, name) for h, name in (await session.execute(stmt)).all()]


async def _load(session, handoff_id: UUID) -> tuple[Handoff, str]:
    row = (
        await session.execute(
            select(Handoff, CustomerAccount.display_name)
            .join(CustomerAccount, CustomerAccount.id == Handoff.customer_account_id)
            .where(Handoff.id == handoff_id)
            .with_for_update(of=Handoff)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such handoff.")
    return row[0], row[1]


@router.post("/{handoff_id}/acknowledge", response_model=HandoffOut)
async def acknowledge(handoff_id: UUID, owner: Owner, session: Session) -> HandoffOut:
    handoff, customer = await _load(session, handoff_id)
    if handoff.status == "resolved":
        raise HTTPException(status.HTTP_409_CONFLICT, "This handoff is already resolved.")
    handoff.status = "acknowledged"
    await session.flush()
    await audit.record(
        session,
        actor_type="owner",
        actor_id=str(owner.owner_id),
        customer_account_id=handoff.customer_account_id,
        action="handoff_acknowledged",
        target=str(handoff.id),
    )
    return _out(handoff, customer)


@router.post("/{handoff_id}/resolve", response_model=HandoffOut)
async def resolve(handoff_id: UUID, owner: Owner, session: Session, state: State) -> HandoffOut:
    handoff, customer = await _load(session, handoff_id)
    if handoff.status == "resolved":
        raise HTTPException(status.HTTP_409_CONFLICT, "This handoff is already resolved.")
    handoff.status = "resolved"
    handoff.resolved_by = owner.owner_id
    handoff.resolved_at = state.clock()
    await session.flush()
    await audit.record(
        session,
        actor_type="owner",
        actor_id=str(owner.owner_id),
        customer_account_id=handoff.customer_account_id,
        action="handoff_resolved",
        target=str(handoff.id),
    )
    return _out(handoff, customer)
