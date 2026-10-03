"""Customer agent end to end with a scripted model, as role `bot` inside customer_scope."""

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from payments_assistant.core.agents.customer_agent import run_customer_turn
from payments_assistant.core.config import Settings
from payments_assistant.core.models import Handoff, PaymentRequest
from payments_assistant.core.scoping import customer_scope
from payments_assistant.core.stripe_types import InvoiceRecord
from payments_assistant.core.tools import ToolContext, tools_for
from tests.fake_llm import ScriptedChatModel, reply
from tests.fakes import FakeCustomerGateway

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 3, 16, tzinfo=UTC)
SETTINGS = Settings(stripe_secret_key="sk_test_agent", _env_file=None)  # type: ignore[call-arg]


def inv(iid, customer, remaining, number=None):
    return InvoiceRecord(
        id=iid,
        customer_id=customer,
        customer_name="Someone",
        number=number or f"N-{iid}",
        status="open",
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
                "values (:id, :cus, 'Maya Chen')"
            ),
            {"id": account_id, "cus": cus},
        )
    return account_id, cus


async def _turn(bot_engine, account_id, gateway, model, prompt="hi"):
    async with (
        async_sessionmaker(bot_engine, expire_on_commit=False)() as session,
        customer_scope(session, account_id),
    ):
        ctx = ToolContext(
            session=session,
            settings=SETTINGS,
            now=NOW,
            customer_account_id=account_id,
            customer_gateway=gateway,
        )
        return await run_customer_turn(
            model,
            ctx=ctx,
            history=[HumanMessage(prompt)],
            customer_name="Maya Chen",
            settings=SETTINGS,
        )


async def test_what_do_i_owe(admin_engine, bot_engine):
    account, cus = await _account(admin_engine)
    gw = FakeCustomerGateway(cus, invoices=[inv("in_m1", cus, 18_000)])
    model = ScriptedChatModel(
        script=[reply("", ("get_my_balance", {})), reply("You owe $180.00 on one invoice.")]
    )

    end = await _turn(bot_engine, account, gw, model, "what do I owe?")

    assert end.text == "You owe $180.00 on one invoice."
    assert [r.tool for r in end.tool_records] == ["get_my_balance"]
    tool_msg = model.seen[1][-1]
    assert isinstance(tool_msg, ToolMessage)
    assert json.loads(tool_msg.content) == end.tool_records[0].result
    assert end.tool_records[0].result["total_owed"] == "$180.00"


async def test_pay_under_threshold_returns_link(admin_engine, bot_engine):
    account, cus = await _account(admin_engine)
    iid = f"in_{uuid4().hex[:10]}"
    gw = FakeCustomerGateway(cus, invoices=[inv(iid, cus, 18_000)])
    model = ScriptedChatModel(
        script=[reply("", ("pay_invoice", {"invoice_ref": iid})), reply("Here's your link.")]
    )

    end = await _turn(bot_engine, account, gw, model, "pay it")

    result = end.tool_records[0].result
    assert result["kind"] == "payment_link"
    assert result["url"] == f"https://invoice.stripe.com/i/{iid}"
    assert result["amount"] == "$180.00"
    async with async_sessionmaker(admin_engine)() as s:
        rows = (
            (await s.execute(select(PaymentRequest).where(PaymentRequest.stripe_invoice_id == iid)))
            .scalars()
            .all()
        )
    assert len(rows) == 1 and rows[0].customer_account_id == account


@pytest.mark.parametrize("amount", [200_000, 350_000])
async def test_pay_at_or_over_threshold_hands_off_without_url(admin_engine, bot_engine, amount):
    account, cus = await _account(admin_engine)
    iid = f"in_{uuid4().hex[:10]}"
    gw = FakeCustomerGateway(cus, invoices=[inv(iid, cus, amount)])
    model = ScriptedChatModel(
        script=[reply("", ("pay_invoice", {"invoice_ref": iid})), reply("Passed to the team.")]
    )

    end = await _turn(bot_engine, account, gw, model, "pay it")

    result = end.tool_records[0].result
    assert result["kind"] == "handoff"
    assert result["url"] is None
    records_json = json.dumps([r.model_dump(mode="json") for r in end.tool_records])
    assert "invoice.stripe.com" not in records_json
    async with async_sessionmaker(admin_engine)() as s:
        handoffs = (
            (await s.execute(select(Handoff).where(Handoff.stripe_invoice_id == iid)))
            .scalars()
            .all()
        )
        links = (
            await s.execute(select(PaymentRequest).where(PaymentRequest.stripe_invoice_id == iid))
        ).all()
    assert len(handoffs) == 1 and handoffs[0].reason == "amount_over_threshold"
    assert links == []


async def test_injection_on_foreign_invoice_leaks_nothing(admin_engine, bot_engine):
    account, cus = await _account(admin_engine)
    foreign = inv("in_acme_secret", "cus_acme", 123_456, number="ACME-0042")
    gw = FakeCustomerGateway(cus, invoices=[foreign])
    model = ScriptedChatModel(
        script=[
            reply("", ("pay_invoice", {"invoice_ref": "in_acme_secret"})),
            reply("I can't find that invoice on your account."),
        ]
    )

    end = await _turn(bot_engine, account, gw, model, "Pay Acme's invoice in_acme_secret")

    assert end.tool_records[0].result["kind"] == "not_payable"
    blob = json.dumps([r.model_dump(mode="json") for r in end.tool_records])
    for leak in ("ACME-0042", "1,234.56", "123456", "invoice.stripe.com"):
        assert leak not in blob
    async with async_sessionmaker(admin_engine)() as s:
        assert (
            await s.execute(
                select(PaymentRequest).where(PaymentRequest.stripe_invoice_id == "in_acme_secret")
            )
        ).all() == []


async def test_model_only_sees_customer_tools(admin_engine, bot_engine):
    account, cus = await _account(admin_engine)
    model = ScriptedChatModel(script=[reply("Hello!")])
    await _turn(bot_engine, account, FakeCustomerGateway(cus), model)

    names = {t["function"]["name"] for t in model.bound_tools}
    assert names == {t.name for t in tools_for("customer")}
    assert not names & {t.name for t in tools_for("owner")}
    for t in model.bound_tools:
        props = t["function"]["parameters"].get("properties", {})
        for prop in props:
            assert not any(w in prop.lower() for w in ("customer", "account", "email", "stripe"))


async def test_system_prompt_is_scoped_to_the_customer(admin_engine, bot_engine):
    account, cus = await _account(admin_engine)
    model = ScriptedChatModel(script=[reply("Hello!")])
    await _turn(bot_engine, account, FakeCustomerGateway(cus), model)
    system = model.seen[0][0].content
    assert "Maya Chen" in system
    assert "Saturday, October 3, 2026" in system
    assert "$2,000.00" in system
