"""Owner read tools over the demo account (no DB: read tools never touch the session)."""

import pytest

from payments_assistant.core.timeutil import Period
from payments_assistant.core.tools import REGISTRY
from payments_assistant.core.tools.registry import ToolContext, ToolError
from tests.demo_data import MAYA_LAST_CHARGE, NOW, owner_gateway


@pytest.fixture
def ctx(settings):
    from uuid import uuid4

    return ToolContext(
        session=None,  # type: ignore[arg-type]
        settings=settings,
        now=NOW,
        owner_id=uuid4(),
        owner_gateway=owner_gateway(),
    )


async def run(ctx, name, **args):
    return await REGISTRY[name].run(ctx, args)


async def test_activity_today_vs_yesterday_so_far(ctx):
    out = await run(ctx, "get_activity", period="today")
    assert out.current.gross == "$822.00"
    assert out.current.successful_payments == 4
    assert out.current.failed_payments == 2
    assert out.current.failures_by_reason == {"insufficient_funds": 2}
    # yesterday until 15:30 only includes the 10:00 payment
    assert out.previous.gross == "$300.00"
    assert out.gross_change == "$522.00"
    assert [i.customer for i in out.open_invoices] == ["Acme Corp"]
    assert out.open_invoices[0].amount_remaining == "$1,200.00"


async def test_last_week_vs_week_before(ctx):
    out = await run(ctx, "get_activity", period=Period.LAST_WEEK.value)
    assert out.current.gross == "$1,000.00"
    assert out.previous.gross == "$800.00"
    assert out.previous.refunded == "$50.00"
    assert out.gross_change_pct == 25.0


async def test_explicit_compare_to(ctx):
    out = await run(ctx, "get_activity", period="today", compare_to="last_week")
    assert out.previous.gross == "$1,000.00"


async def test_find_mayas_last_payment(ctx):
    out = await run(ctx, "find_payments", customer_query="maya", status="succeeded", limit=1)
    assert [p.charge_id for p in out.payments] == [MAYA_LAST_CHARGE]
    assert out.payments[0].amount == "$82.00"
    assert out.payments[0].refundable == "$82.00"


async def test_find_payments_failed_shows_decline_reason(ctx):
    out = await run(ctx, "find_payments", period="today", status="failed")
    assert {p.decline_reason for p in out.payments} == {"insufficient_funds"}


async def test_unknown_customer_is_a_tool_error(ctx):
    with pytest.raises(ToolError, match="No customer matches"):
        await run(ctx, "find_payments", customer_query="Zebra Ltd")


async def test_list_open_invoices_for_customer(ctx):
    out = await run(ctx, "list_invoices", customer_query="acme")
    assert [i.invoice_id for i in out.invoices] == ["in_acme_1200"]
    assert out.invoices[0].due_date == "2026-10-09"  # midnight UTC = evening of 10/9 in NY


async def test_find_customers(ctx):
    out = await run(ctx, "find_customers", query="blue")
    assert [c.customer_id for c in out.customers] == ["cus_blue"]
