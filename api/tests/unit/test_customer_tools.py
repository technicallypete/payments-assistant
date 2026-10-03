"""Customer read tools: correct formatting, and never anything belonging to another customer."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from payments_assistant.core.stripe_types import InvoiceRecord, PaymentRecord
from payments_assistant.core.tools import REGISTRY, ToolContext
from tests.fakes import FakeCustomerGateway

NOW = datetime(2026, 10, 3, 16, tzinfo=UTC)  # 12:00 in New York
MAYA, ACME = "cus_maya", "cus_acme"


def inv(iid, customer, remaining, status="open", number=None, due=None):
    return InvoiceRecord(
        id=iid,
        customer_id=customer,
        customer_name="Maya Chen" if customer == MAYA else "Acme Corp",
        number=number or f"N-{iid}",
        status=status,
        currency="usd",
        amount_due=remaining,
        amount_paid=0,
        amount_remaining=remaining,
        due_date=due,
        hosted_invoice_url=f"https://invoice.stripe.com/i/{iid}",
        created=NOW,
    )


def pay(pid, customer, amount, status="succeeded", decline=None, when=NOW):
    return PaymentRecord(
        id=pid,
        customer_id=customer,
        customer_name="x",
        amount=amount,
        currency="usd",
        status=status,
        failure_code="card_declined" if status == "failed" else None,
        decline_code=decline,
        occurred_at=when,
    )


@pytest.fixture
def gateway():
    return FakeCustomerGateway(
        MAYA,
        invoices=[
            inv("in_maya_open", MAYA, 18_000, due=datetime(2026, 10, 17, 3, tzinfo=UTC)),
            inv("in_maya_paid", MAYA, 0, status="paid"),
            # Foreign data the gateway holds but must never return:
            inv("in_acme_1", ACME, 120_000, number="ACME-SECRET-1"),
            inv("in_acme_2", ACME, 350_000, number="ACME-SECRET-2"),
        ],
        payments=[
            pay("ch_m1", MAYA, 4_500, when=NOW - timedelta(days=1)),
            pay("ch_m2", MAYA, 9_900, status="failed", decline="insufficient_funds"),
            pay("ch_a1", ACME, 777_700),
        ],
    )


@pytest.fixture
def ctx(settings, gateway):
    return ToolContext(
        session=None,  # type: ignore[arg-type]  # read tools never touch the DB
        settings=settings,
        now=NOW,
        customer_account_id=uuid4(),
        customer_gateway=gateway,
    )


def _no_foreign(output) -> None:
    blob = json.dumps(output.model_dump(mode="json"))
    for leak in ("acme", "ACME-SECRET", "1,200", "3,500", "7,777", "in_acme", "ch_a1"):
        assert leak.lower() not in blob.lower(), f"leaked {leak}: {blob}"


async def test_balance_totals_only_own_open_invoices(ctx):
    out = await REGISTRY["get_my_balance"].run(ctx, {})
    assert out.total_owed == "$180.00"
    assert out.open_invoice_count == 1
    assert out.open_invoices[0].invoice_id == "in_maya_open"
    # Due 03:00 UTC on the 17th is still the 16th in New York.
    assert str(out.open_invoices[0].due_date) == "2026-10-16"
    _no_foreign(out)


async def test_balance_when_nothing_owed(settings):
    ctx = ToolContext(
        session=None,  # type: ignore[arg-type]
        settings=settings,
        now=NOW,
        customer_account_id=uuid4(),
        customer_gateway=FakeCustomerGateway(MAYA, invoices=[inv("in_acme", ACME, 5_000)]),
    )
    out = await REGISTRY["get_my_balance"].run(ctx, {})
    assert out.total_owed == "$0.00" and out.open_invoice_count == 0
    _no_foreign(out)


async def test_list_invoices_open_by_default(ctx):
    out = await REGISTRY["list_my_invoices"].run(ctx, {})
    assert [i.invoice_id for i in out.invoices] == ["in_maya_open"]
    _no_foreign(out)


async def test_list_invoices_include_paid(ctx):
    out = await REGISTRY["list_my_invoices"].run(ctx, {"include_paid": True})
    assert {i.invoice_id for i in out.invoices} == {"in_maya_open", "in_maya_paid"}
    _no_foreign(out)


async def test_list_payments_newest_first_with_decline_reason(ctx):
    out = await REGISTRY["list_my_payments"].run(ctx, {"limit": 5})
    assert [p.amount for p in out.payments] == ["$99.00", "$45.00"]
    assert out.payments[0].status == "failed"
    assert out.payments[0].decline_reason == "insufficient_funds"
    assert out.payments[1].decline_reason is None
    _no_foreign(out)


@pytest.mark.parametrize("limit", [0, 21])
async def test_list_payments_limit_bounds(ctx, limit):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        await REGISTRY["list_my_payments"].run(ctx, {"limit": limit})


async def test_customer_tool_without_customer_context_is_refused(settings):
    ctx = ToolContext(session=None, settings=settings, now=NOW)  # type: ignore[arg-type]
    with pytest.raises(PermissionError):
        await REGISTRY["get_my_balance"].run(ctx, {})


def test_customer_tool_inputs_have_no_customer_identifying_fields():
    forbidden = ("customer", "account", "email", "stripe", "cus_", "name", "phone", "user")
    for spec in REGISTRY.values():
        if spec.audience != "customer":
            continue
        props = spec.json_schema()["function"]["parameters"].get("properties", {})
        for prop in props:
            assert not any(f in prop.lower() for f in forbidden), (spec.name, prop)


# --- pay_invoice reference resolution (no DB: resolution happens before any write)

from payments_assistant.core.tools.customer import _resolve_invoice_ref  # noqa: E402
from payments_assistant.core.tools.registry import ToolError  # noqa: E402


async def test_ref_none_uses_the_only_open_invoice():
    gw = FakeCustomerGateway(MAYA, invoices=[inv("in_m1", MAYA, 18_000, number="MAYA-0007")])
    assert await _resolve_invoice_ref(gw, None) == "in_m1"


async def test_ref_none_with_several_open_asks_which():
    gw = FakeCustomerGateway(MAYA, invoices=[inv("in_m1", MAYA, 100), inv("in_m2", MAYA, 200)])
    with pytest.raises(ToolError, match="more than one"):
        await _resolve_invoice_ref(gw, None)


async def test_ref_none_with_nothing_open():
    with pytest.raises(ToolError, match="don't have any open invoices"):
        await _resolve_invoice_ref(FakeCustomerGateway(MAYA, invoices=[]), None)


async def test_ref_by_invoice_number_case_insensitive():
    gw = FakeCustomerGateway(MAYA, invoices=[inv("in_m1", MAYA, 18_000, number="MAYA-0007")])
    assert await _resolve_invoice_ref(gw, "maya-0007") == "in_m1"


async def test_ref_number_of_foreign_invoice_does_not_resolve():
    gw = FakeCustomerGateway(MAYA, invoices=[inv("in_a1", ACME, 120_000, number="ACME-0001")])
    # Not resolved to the foreign id; passes through and later gets the generic not-payable answer.
    assert await _resolve_invoice_ref(gw, "ACME-0001") == "ACME-0001"
