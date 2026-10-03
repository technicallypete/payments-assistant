"""Customer accounts for the owner: who they are, what they owe, and Telegram invite links."""

from collections import defaultdict
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from payments_assistant.core.models import CustomerAccount, TelegramIdentity
from payments_assistant.core.money import format_money
from payments_assistant.core.services import accounts, audit, invites
from payments_assistant.http.deps import Owner, Session, State

router = APIRouter(prefix="/customers", tags=["customers"])


class CustomerOut(BaseModel):
    id: UUID
    display_name: str
    email: str | None
    stripe_customer_id: str
    status: str
    amount_owed: str
    amount_owed_cents: int
    telegram_linked: bool


class InviteOut(BaseModel):
    url: str
    expires_at: datetime


class SyncOut(BaseModel):
    synced: int


@router.get("", response_model=list[CustomerOut])
async def list_customers(owner: Owner, session: Session, state: State) -> list[CustomerOut]:
    owed: dict[str, int] = defaultdict(int)
    for inv in await state.owner_gateway().list_invoices(status="open"):
        if inv.currency.lower() == state.settings.default_currency.lower():
            owed[inv.customer_id] += inv.amount_remaining

    linked = set(
        (
            await session.execute(
                select(TelegramIdentity.customer_account_id).where(
                    TelegramIdentity.revoked_at.is_(None)
                )
            )
        ).scalars()
    )
    rows = (
        (await session.execute(select(CustomerAccount).order_by(CustomerAccount.display_name)))
        .scalars()
        .all()
    )
    currency = state.settings.default_currency
    return [
        CustomerOut(
            id=a.id,
            display_name=a.display_name,
            email=a.email,
            stripe_customer_id=a.stripe_customer_id,
            status=a.status,
            amount_owed=format_money(owed.get(a.stripe_customer_id, 0), currency),
            amount_owed_cents=owed.get(a.stripe_customer_id, 0),
            telegram_linked=a.id in linked,
        )
        for a in rows
    ]


@router.post("/{account_id}/invite", response_model=InviteOut)
async def create_invite(
    account_id: UUID, owner: Owner, session: Session, state: State
) -> InviteOut:
    account = await session.get(CustomerAccount, account_id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such customer.")
    if account.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "This customer account is disabled.")
    if not state.settings.telegram_bot_username:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "TELEGRAM_BOT_USERNAME is not configured."
        )
    invite = await invites.create_invite(
        session,
        customer_account_id=account.id,
        bot_username=state.settings.telegram_bot_username,
        ttl_days=state.settings.invite_ttl_days,
        created_by=owner.owner_id,
        now=state.clock(),
    )
    await audit.record(
        session,
        actor_type="owner",
        actor_id=str(owner.owner_id),
        customer_account_id=account.id,
        action="invite_created",
        target=str(account.id),
    )
    return InviteOut(url=invite.url, expires_at=invite.expires_at)


@router.post("/sync", response_model=SyncOut)
async def sync_customers(owner: Owner, session: Session, state: State) -> SyncOut:
    """Upsert an account for every Stripe customer, so ones created in Stripe show up here."""
    customers = await state.owner_gateway().list_customers()
    for c in customers:
        await accounts.upsert_from_stripe(session, c)
    return SyncOut(synced=len(customers))
