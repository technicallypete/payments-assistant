"""Customer payment flow, run as the `bot` role inside customer_scope (the real production path)."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from payments_assistant.core.models import Handoff, PaymentRequest
from payments_assistant.core.scoping import customer_scope
from payments_assistant.core.services.payments import (
    request_customer_payment,
    request_human,
    requires_handoff,
)
from payments_assistant.core.stripe_types import InvoiceRecord
from tests.fakes import FakeCustomerGateway

pytestmark = pytest.mark.integration

THRESHOLD = 200_000
NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)


def invoice(iid: str, customer: str, remaining: int, status: str = "open") -> InvoiceRecord:
    return InvoiceRecord(
        id=iid,
        customer_id=customer,
        customer_name="Someone",
        number=f"INV-{iid}",
        status=status,
        currency="usd",
        amount_due=remaining,
        amount_paid=0,
        amount_remaining=remaining,
        hosted_invoice_url=f"https://invoice.stripe.com/i/{iid}",
        created=NOW,
    )


async def _account(admin_engine) -> tuple[UUID, str]:
    account_id, cus = uuid4(), f"cus_{uuid4().hex[:12]}"
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "insert into customer_accounts (id, stripe_customer_id, display_name) "
                "values (:id, :cus, 'C')"
            ),
            {"id": account_id, "cus": cus},
        )
    return account_id, cus


@pytest.mark.parametrize(
    "amount,expected", [(199_999, False), (200_000, True), (200_001, True), (1, False)]
)
def test_threshold_boundary(amount, expected):
    assert requires_handoff(amount, THRESHOLD) is expected


async def _pay(bot_engine, account_id, gateway, invoice_id):
    async with (
        async_sessionmaker(bot_engine, expire_on_commit=False)() as session,
        customer_scope(session, account_id),
    ):
        return await request_customer_payment(
            session,
            gateway,
            customer_account_id=account_id,
            invoice_id=invoice_id,
            conversation_id=None,
            threshold_cents=THRESHOLD,
        )


async def test_under_threshold_issues_link_once(admin_engine, bot_engine):
    account, cus = await _account(admin_engine)
    iid = f"in_{uuid4().hex[:10]}"
    gw = FakeCustomerGateway(cus, invoices=[invoice(iid, cus, 199_999)])

    first = await _pay(bot_engine, account, gw, iid)
    second = await _pay(bot_engine, account, gw, iid)

    assert first.kind == "payment_link"
    assert first.url == f"https://invoice.stripe.com/i/{iid}"
    assert first.amount == 199_999
    assert second.url == first.url
    async with admin_engine.connect() as conn:
        n = (
            await conn.execute(
                select(PaymentRequest).where(PaymentRequest.stripe_invoice_id == iid)
            )
        ).all()
    assert len(n) == 1  # reused, not duplicated


@pytest.mark.parametrize("amount", [200_000, 350_000])
async def test_at_or_over_threshold_hands_off_without_url(admin_engine, bot_engine, amount):
    account, cus = await _account(admin_engine)
    iid = f"in_{uuid4().hex[:10]}"
    gw = FakeCustomerGateway(cus, invoices=[invoice(iid, cus, amount)])

    outcome = await _pay(bot_engine, account, gw, iid)
    again = await _pay(bot_engine, account, gw, iid)

    assert outcome.kind == "handoff"
    assert outcome.url is None
    assert outcome.handoff_id is not None
    assert again.handoff_id == outcome.handoff_id  # no duplicate handoffs
    async with async_sessionmaker(admin_engine)() as session:
        rows = (
            (await session.execute(select(Handoff).where(Handoff.stripe_invoice_id == iid)))
            .scalars()
            .all()
        )
        links = (
            await session.execute(
                select(PaymentRequest).where(PaymentRequest.stripe_invoice_id == iid)
            )
        ).all()
    assert len(rows) == 1 and rows[0].reason == "amount_over_threshold"
    assert rows[0].amount == amount
    assert links == []


async def test_other_customers_invoice_is_not_payable_and_leaks_nothing(admin_engine, bot_engine):
    account, cus = await _account(admin_engine)
    other_iid = f"in_{uuid4().hex[:10]}"
    # The gateway knows an invoice that belongs to someone else.
    gw = FakeCustomerGateway(cus, invoices=[invoice(other_iid, "cus_someone_else", 5_000)])

    outcome = await _pay(bot_engine, account, gw, other_iid)
    assert outcome.kind == "not_payable"
    assert outcome.url is None and outcome.amount == 0


async def test_unknown_invoice_is_not_payable(admin_engine, bot_engine):
    account, cus = await _account(admin_engine)
    outcome = await _pay(bot_engine, account, FakeCustomerGateway(cus, invoices=[]), "in_nope")
    assert outcome.kind == "not_payable"


@pytest.mark.parametrize(
    "status,remaining,kind", [("paid", 0, "already_paid"), ("void", 500, "not_payable")]
)
async def test_closed_invoices(admin_engine, bot_engine, status, remaining, kind):
    account, cus = await _account(admin_engine)
    iid = f"in_{uuid4().hex[:10]}"
    gw = FakeCustomerGateway(cus, invoices=[invoice(iid, cus, remaining, status=status)])
    assert (await _pay(bot_engine, account, gw, iid)).kind == kind


async def test_request_human_creates_handoff(admin_engine, bot_engine):
    account, _ = await _account(admin_engine)
    async with (
        async_sessionmaker(bot_engine, expire_on_commit=False)() as session,
        customer_scope(session, account),
    ):
        handoff = await request_human(
            session, customer_account_id=account, conversation_id=None, summary="Disputes fee"
        )
    async with async_sessionmaker(admin_engine)() as session:
        row = (await session.execute(select(Handoff).where(Handoff.id == handoff.id))).scalar_one()
    assert row.reason == "customer_requested" and row.summary == "Disputes fee"
