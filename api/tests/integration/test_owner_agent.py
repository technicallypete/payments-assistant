"""Owner agent turns end to end (scripted model, fake Stripe, real DB as role `api`)."""

from datetime import date
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import HumanMessage
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from payments_assistant.core.agents.events import ActionProposed, MessageEnd
from payments_assistant.core.agents.owner_agent import owner_system_prompt, run_owner_turn
from payments_assistant.core.models import OwnerAction
from payments_assistant.core.tools import ToolContext
from tests.demo_data import MAYA_LAST_CHARGE, NOW, owner_gateway
from tests.fake_llm import ScriptedChatModel, reply

pytestmark = pytest.mark.integration


async def _owner(admin_engine) -> UUID:
    owner_id = uuid4()
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("insert into owners (id, email, password_hash) values (:id, :e, 'x')"),
            {"id": owner_id, "e": f"{owner_id.hex}@example.com"},
        )
    return owner_id


async def _turn(api_engine, settings, owner_id, gw, script, prompt):
    model = ScriptedChatModel(script=script)
    maker = async_sessionmaker(api_engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        ctx = ToolContext(
            session=session, settings=settings, now=NOW, owner_id=owner_id, owner_gateway=gw
        )
        events = [
            e
            async for e in run_owner_turn(
                model, ctx=ctx, history=[HumanMessage(prompt)], model_name="scripted"
            )
        ]
    return model, events


async def test_refund_mayas_last_payment_only_proposes(admin_engine, api_engine, settings):
    owner, gw = await _owner(admin_engine), owner_gateway()
    script = [
        reply("", ("find_payments", {"customer_query": "Maya", "status": "succeeded", "limit": 1})),
        reply("", ("propose_refund", {"charge_id": MAYA_LAST_CHARGE})),
        reply("Ready to refund $82.00 to Maya. Press Confirm."),
    ]
    _, events = await _turn(api_engine, settings, owner, gw, script, "Refund Maya's last payment")

    proposed = [e for e in events if isinstance(e, ActionProposed)]
    assert len(proposed) == 1
    assert proposed[0].preview.startswith("Refund $82.00 to Maya Chen")
    assert gw.calls == []  # nothing happened in Stripe

    async with async_sessionmaker(admin_engine)() as s:
        action = (
            await s.execute(
                select(OwnerAction).where(OwnerAction.id == UUID(proposed[0].action_id))
            )
        ).scalar_one()
    assert action.status == "proposed"
    assert action.params == {"charge_id": MAYA_LAST_CHARGE, "amount": None}


async def test_invoice_acme_due_next_friday(admin_engine, api_engine, settings):
    owner, gw = await _owner(admin_engine), owner_gateway()
    script = [
        reply("", ("find_customers", {"query": "Acme Corp"})),
        reply(
            "",
            (
                "propose_invoice",
                {
                    "customer_id": "cus_acme",
                    "amount_cents": 25_000,
                    "description": "Services",
                    "due": "next Friday",
                },
            ),
        ),
        reply("Drafted. Confirm to send it."),
    ]
    _, events = await _turn(
        api_engine,
        settings,
        owner,
        gw,
        script,
        "Create a $250 invoice for Acme Corp due next Friday",
    )
    proposed = next(e for e in events if isinstance(e, ActionProposed))
    assert proposed.preview == "Invoice Acme Corp $250.00 for “Services”, due Fri Oct 9, 2026"
    async with async_sessionmaker(admin_engine)() as s:
        action = (
            await s.execute(select(OwnerAction).where(OwnerAction.id == UUID(proposed.action_id)))
        ).scalar_one()
    assert action.params["due_date"] == date(2026, 10, 9).isoformat()
    assert action.params["amount"] == 25_000


async def test_over_refund_is_rejected_back_to_model(admin_engine, api_engine, settings):
    owner, gw = await _owner(admin_engine), owner_gateway()
    script = [
        reply("", ("propose_refund", {"charge_id": MAYA_LAST_CHARGE, "amount_cents": 10_000})),
        reply("That's more than the payment."),
    ]
    _, events = await _turn(api_engine, settings, owner, gw, script, "refund $100 of maya's last")
    end = events[-1]
    assert isinstance(end, MessageEnd)
    assert "At most $82.00" in end.tool_records[0].error
    assert not [e for e in events if isinstance(e, ActionProposed)]


async def test_system_prompt_has_local_date(settings):
    prompt = owner_system_prompt(now=NOW, timezone="America/New_York", currency="usd")
    assert "Saturday, October 3, 2026" in prompt
    assert "propose_" in prompt


async def test_invoice_description_defaults(admin_engine, api_engine, settings):
    owner, gw = await _owner(admin_engine), owner_gateway()
    script = [
        reply(
            "",
            (
                "propose_invoice",
                {"customer_id": "cus_acme", "amount_cents": 25_000, "due": "friday"},
            ),
        ),
        reply("Drafted."),
    ]
    _, events = await _turn(api_engine, settings, owner, gw, script, "invoice acme $250 friday")
    proposed = next(e for e in events if isinstance(e, ActionProposed))
    assert "\u201cServices\u201d" in proposed.preview
