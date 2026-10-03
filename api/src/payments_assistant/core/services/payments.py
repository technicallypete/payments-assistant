"""Customer-side payment flow (spec §7). The $2,000 rule lives HERE, not in any prompt.

Callers run these inside `customer_scope(session, account_id)` with a `CustomerStripeGateway`
bound to the same customer, so both the DB (RLS) and Stripe access are pinned to one customer.
"""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from payments_assistant.core.models import Handoff, PaymentRequest
from payments_assistant.core.services import audit
from payments_assistant.core.stripe_gateway import CustomerStripeGateway
from payments_assistant.core.stripe_types import ForeignObjectError, InvoiceRecord

OutcomeKind = Literal["payment_link", "handoff", "already_paid", "not_payable"]


@dataclass(frozen=True)
class PaymentOutcome:
    kind: OutcomeKind
    invoice_id: str
    amount: int = 0
    currency: str = ""
    url: str | None = None  # only for kind == "payment_link"
    handoff_id: UUID | None = None


def requires_handoff(amount: int, threshold_cents: int) -> bool:
    """Payments of $2,000.00 or more are never completed by the bot."""
    return amount >= threshold_cents


async def request_customer_payment(
    session: AsyncSession,
    gateway: CustomerStripeGateway,
    *,
    customer_account_id: UUID,
    invoice_id: str,
    conversation_id: UUID | None,
    threshold_cents: int,
) -> PaymentOutcome:
    try:
        invoice = await gateway.get_invoice(invoice_id)
    except ForeignObjectError:
        # Same answer as "doesn't exist": never confirm another customer's invoice exists.
        return PaymentOutcome(kind="not_payable", invoice_id=invoice_id)

    if invoice.status == "paid" or (invoice.status == "open" and invoice.amount_remaining == 0):
        return PaymentOutcome(kind="already_paid", invoice_id=invoice.id, currency=invoice.currency)
    if invoice.status != "open" or not invoice.hosted_invoice_url:
        return PaymentOutcome(kind="not_payable", invoice_id=invoice.id)

    if requires_handoff(invoice.amount_remaining, threshold_cents):
        handoff = await _open_handoff_for(session, customer_account_id, invoice, conversation_id)
        return PaymentOutcome(
            kind="handoff",
            invoice_id=invoice.id,
            amount=invoice.amount_remaining,
            currency=invoice.currency,
            handoff_id=handoff.id,
        )

    request = await _open_payment_request_for(
        session, customer_account_id, invoice, conversation_id
    )
    return PaymentOutcome(
        kind="payment_link",
        invoice_id=invoice.id,
        amount=request.amount,
        currency=request.currency,
        url=request.hosted_url,
    )


async def request_human(
    session: AsyncSession,
    *,
    customer_account_id: UUID,
    conversation_id: UUID | None,
    summary: str,
) -> Handoff:
    handoff = Handoff(
        customer_account_id=customer_account_id,
        conversation_id=conversation_id,
        reason="customer_requested",
        summary=summary,
    )
    session.add(handoff)
    await session.flush()
    await audit.record(
        session,
        actor_type="customer",
        actor_id=str(customer_account_id),
        customer_account_id=customer_account_id,
        action="handoff_requested",
        target=str(handoff.id),
    )
    return handoff


async def _open_handoff_for(
    session: AsyncSession,
    customer_account_id: UUID,
    invoice: InvoiceRecord,
    conversation_id: UUID | None,
) -> Handoff:
    existing = (
        await session.execute(
            select(Handoff).where(
                Handoff.stripe_invoice_id == invoice.id,
                Handoff.reason == "amount_over_threshold",
                Handoff.status != "resolved",
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    handoff = Handoff(
        customer_account_id=customer_account_id,
        conversation_id=conversation_id,
        reason="amount_over_threshold",
        stripe_invoice_id=invoice.id,
        amount=invoice.amount_remaining,
        currency=invoice.currency,
        summary=(
            f"Customer asked to pay invoice {invoice.number or invoice.id} "
            f"({invoice.amount_remaining / 100:,.2f} {invoice.currency.upper()}), "
            "which is at or above the self-serve limit."
        ),
    )
    session.add(handoff)
    await session.flush()
    await audit.record(
        session,
        actor_type="customer",
        actor_id=str(customer_account_id),
        customer_account_id=customer_account_id,
        action="handoff_created",
        target=invoice.id,
        payload={"amount": invoice.amount_remaining, "reason": "amount_over_threshold"},
    )
    return handoff


async def _open_payment_request_for(
    session: AsyncSession,
    customer_account_id: UUID,
    invoice: InvoiceRecord,
    conversation_id: UUID | None,
) -> PaymentRequest:
    existing = (
        await session.execute(
            select(PaymentRequest).where(
                PaymentRequest.stripe_invoice_id == invoice.id,
                PaymentRequest.status == "link_sent",
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    request = PaymentRequest(
        customer_account_id=customer_account_id,
        conversation_id=conversation_id,
        stripe_invoice_id=invoice.id,
        amount=invoice.amount_remaining,
        currency=invoice.currency,
        hosted_url=invoice.hosted_invoice_url,
    )
    session.add(request)
    await session.flush()
    await audit.record(
        session,
        actor_type="customer",
        actor_id=str(customer_account_id),
        customer_account_id=customer_account_id,
        action="payment_link_issued",
        target=invoice.id,
        payload={"amount": invoice.amount_remaining},
    )
    return request
