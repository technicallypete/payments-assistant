"""The test fakes behave like the live gateways where it matters (ownership, idempotency)."""

from datetime import UTC, date, datetime, timedelta

import pytest

from payments_assistant.core.stripe_types import (
    CustomerInfo,
    ForeignObjectError,
    InvoiceRecord,
    PaymentRecord,
    StripeObjectNotFound,
)
from tests.fakes import FakeCustomerGateway, FakeOwnerGateway

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)


def payment(pid: str, customer: str, amount: int = 5_000, when: datetime = NOW) -> PaymentRecord:
    return PaymentRecord(
        id=pid,
        customer_id=customer,
        customer_name=customer,
        amount=amount,
        currency="usd",
        status="succeeded",
        occurred_at=when,
    )


def invoice(iid: str, customer: str, status: str = "open") -> InvoiceRecord:
    return InvoiceRecord(
        id=iid,
        customer_id=customer,
        customer_name=customer,
        number=None,
        status=status,
        currency="usd",
        amount_due=1_000,
        amount_paid=0,
        amount_remaining=1_000,
        created=NOW,
    )


async def test_refund_is_idempotent_and_recorded_once():
    gw = FakeOwnerGateway(payments=[payment("ch_1", "cus_a")])
    first = await gw.refund(charge_id="ch_1", amount=None, idempotency_key="k1")
    again = await gw.refund(charge_id="ch_1", amount=None, idempotency_key="k1")
    assert first == again and first.id.startswith("re_")
    assert len(gw.calls) == 1
    assert gw.calls[0] == {
        "method": "refund",
        "idempotency_key": "k1",
        "charge_id": "ch_1",
        "amount": None,
    }
    assert (await gw.get_payment("ch_1")).amount_refunded == 5_000


async def test_refund_unknown_charge_raises_without_recording():
    gw = FakeOwnerGateway()
    with pytest.raises(StripeObjectNotFound):
        await gw.refund(charge_id="ch_nope", amount=None, idempotency_key="k")
    assert gw.calls == []


async def test_create_invoice_and_payment_link_ids():
    gw = FakeOwnerGateway(customers=[CustomerInfo(id="cus_a", name="Acme")])
    inv = await gw.create_invoice(
        customer_id="cus_a",
        amount=25_000,
        currency="usd",
        description="x",
        due_date=date(2026, 10, 9),
        idempotency_key="i1",
    )
    assert inv.id.startswith("in_") and inv in await gw.list_invoices(customer_id="cus_a")
    assert inv.customer_name == "Acme"
    assert (
        await gw.create_invoice(
            customer_id="cus_a",
            amount=25_000,
            currency="usd",
            description="x",
            due_date=date(2026, 10, 9),
            idempotency_key="i1",
        )
    ).id == inv.id
    link = await gw.create_payment_link(
        amount=100, currency="usd", description="y", idempotency_key="p1"
    )
    assert link.id.startswith("plink_")
    assert [c["method"] for c in gw.calls] == ["create_invoice", "create_payment_link"]


async def test_owner_list_payments_window_and_customer_filter():
    gw = FakeOwnerGateway(
        payments=[
            payment("ch_old", "cus_a", when=NOW - timedelta(days=2)),
            payment("ch_new", "cus_a", when=NOW),
            payment("ch_b", "cus_b", when=NOW),
        ]
    )
    window = await gw.list_payments(start=NOW - timedelta(days=1), end=NOW + timedelta(hours=1))
    assert {p.id for p in window} == {"ch_new", "ch_b"}
    only_a = await gw.list_payments(
        start=NOW - timedelta(days=3), end=NOW + timedelta(hours=1), customer_id="cus_a"
    )
    assert [p.id for p in only_a] == ["ch_new", "ch_old"]


async def test_customer_gateway_ownership():
    gw = FakeCustomerGateway(
        "cus_a",
        invoices=[
            invoice("in_a", "cus_a"),
            invoice("in_b", "cus_b"),
            invoice("in_paid", "cus_a", "paid"),
        ],
        payments=[payment("ch_a", "cus_a"), payment("ch_b", "cus_b")],
    )
    assert {i.id for i in await gw.list_invoices()} == {"in_a", "in_paid"}
    assert [i.id for i in await gw.list_invoices(open_only=True)] == ["in_a"]
    assert [p.id for p in await gw.list_payments()] == ["ch_a"]
    for foreign in ("in_b", "in_missing"):
        with pytest.raises(ForeignObjectError):
            await gw.get_invoice(foreign)
