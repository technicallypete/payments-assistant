"""Stripe webhook processing (spec §5.3). Signature verification happens in the HTTP route.

Idempotent: `stripe_events` is keyed by event id and an event is processed only while
`processed_at` is NULL, so Stripe's retries (and `stripe listen` replays) can't double-apply.
Customer notifications are RETURNED, not sent: the caller sends them only after commit, so a
rolled-back transaction can never tell a customer their payment arrived.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from payments_assistant.core.models import (
    CustomerAccount,
    Handoff,
    PaymentRequest,
    StripeEvent,
    TelegramIdentity,
)
from payments_assistant.core.money import format_money
from payments_assistant.core.services import audit, summaries

HANDLED = {
    "invoice.paid",
    "invoice.payment_failed",
    "invoice.voided",
    "invoice.marked_uncollectible",
    "charge.refunded",
    "payment_intent.succeeded",
    "payment_intent.payment_failed",
}


@dataclass(frozen=True)
class Notification:
    chat_id: int
    text: str


@dataclass(frozen=True)
class Outcome:
    duplicate: bool
    handled: bool
    notifications: list[Notification]


async def process_event(
    session: AsyncSession, event: dict[str, Any], *, now: datetime, timezone: str
) -> Outcome:
    event_id, kind = event["id"], event["type"]
    await session.execute(
        insert(StripeEvent)
        .values(
            event_id=event_id,
            type=kind,
            livemode=bool(event.get("livemode")),
            payload=event,
            received_at=now,
        )
        .on_conflict_do_nothing(index_elements=[StripeEvent.event_id])
    )
    row = (
        await session.execute(
            select(StripeEvent).where(StripeEvent.event_id == event_id).with_for_update()
        )
    ).scalar_one()
    if row.processed_at is not None:
        return Outcome(duplicate=True, handled=False, notifications=[])

    notes: list[Notification] = []
    obj = event.get("data", {}).get("object", {})
    if kind in HANDLED:
        # Anything that moves money changes "today": drop the cached summary.
        await summaries.invalidate_today(session, now=now, tz=timezone)
    match kind:
        case "invoice.paid":
            notes = await _invoice_paid(session, obj, now)
        case "invoice.payment_failed":
            notes = await _invoice_failed(session, obj, now)
        case "invoice.voided" | "invoice.marked_uncollectible":
            await _set_request_status(session, obj["id"], "void", now)
    row.processed_at = now
    row.error = None
    await audit.record(
        session,
        actor_type="stripe",
        actor_id=event_id,
        action=f"webhook:{kind}",
        target=obj.get("id"),
    )
    return Outcome(duplicate=False, handled=kind in HANDLED, notifications=notes)


async def _set_request_status(
    session: AsyncSession, invoice_id: str, status: str, now: datetime
) -> list[UUID]:
    """Move open payment requests for the invoice to `status`; return affected customer ids."""
    result = await session.execute(
        update(PaymentRequest)
        .where(
            PaymentRequest.stripe_invoice_id == invoice_id,
            PaymentRequest.status == "link_sent",
        )
        .values(status=status, updated_at=now)
        .returning(PaymentRequest.customer_account_id)
    )
    return list(result.scalars())


async def _chats_for_invoice(session: AsyncSession, customer_stripe_id: str | None) -> list[int]:
    if not customer_stripe_id:
        return []
    return list(
        (
            await session.execute(
                select(TelegramIdentity.telegram_chat_id)
                .join(CustomerAccount, CustomerAccount.id == TelegramIdentity.customer_account_id)
                .where(
                    CustomerAccount.stripe_customer_id == customer_stripe_id,
                    TelegramIdentity.revoked_at.is_(None),
                )
            )
        ).scalars()
    )


def _label(obj: dict[str, Any]) -> str:
    return obj.get("number") or "your invoice"


async def _invoice_paid(
    session: AsyncSession, obj: dict[str, Any], now: datetime
) -> list[Notification]:
    invoice_id = obj["id"]
    await _set_request_status(session, invoice_id, "paid", now)
    # If the owner collected a ≥ threshold invoice another way, its handoff is done too.
    await session.execute(
        update(Handoff)
        .where(Handoff.stripe_invoice_id == invoice_id, Handoff.status != "resolved")
        .values(status="resolved", resolved_at=now)
    )
    paid = format_money(int(obj.get("amount_paid") or 0), obj.get("currency") or "usd")
    text = f"Payment received ✅ Thanks! {_label(obj)} ({paid}) is paid in full."
    return [Notification(c, text) for c in await _chats_for_invoice(session, obj.get("customer"))]


async def _invoice_failed(
    session: AsyncSession, obj: dict[str, Any], now: datetime
) -> list[Notification]:
    await _set_request_status(session, obj["id"], "failed", now)
    text = (
        f"Your payment for {_label(obj)} didn't go through. You can try again from the same "
        "secure link, or use a different card."
    )
    return [Notification(c, text) for c in await _chats_for_invoice(session, obj.get("customer"))]
