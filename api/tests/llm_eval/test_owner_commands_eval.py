"""Owner natural-language commands against the real model (goal §D: ≥ 90% of this set).

Uses the fixed demo account (tests/demo_data.py, clock Sat 2026-10-03 15:30 New York) so expected
figures are exact. Checks *what the model did* (tool calls, proposals) more than its wording.
"""

import json
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import HumanMessage
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from payments_assistant.core.agents.events import ActionProposed, MessageEnd
from payments_assistant.core.agents.owner_agent import run_owner_turn
from payments_assistant.core.config import Settings
from payments_assistant.core.llm import get_chat_model
from payments_assistant.core.tools import ToolContext
from tests.demo_data import MAYA_LAST_CHARGE, NOW, owner_gateway

pytestmark = pytest.mark.llm_eval


@pytest.fixture
async def owner(admin_engine) -> UUID:
    owner_id = uuid4()
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("insert into owners (id, email, password_hash) values (:id, :e, 'x')"),
            {"id": owner_id, "e": f"{owner_id.hex}@example.com"},
        )
    return owner_id


async def turn(api_engine, owner_id, prompt):
    settings = Settings()
    gw = owner_gateway()
    model = get_chat_model(settings)
    maker = async_sessionmaker(api_engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        ctx = ToolContext(
            session=session, settings=settings, now=NOW, owner_id=owner_id, owner_gateway=gw
        )
        events = [
            e
            async for e in run_owner_turn(
                model, ctx=ctx, history=[HumanMessage(prompt)], model_name=settings.llm_model
            )
        ]
    end = next(e for e in events if isinstance(e, MessageEnd))
    proposals = [e for e in events if isinstance(e, ActionProposed)]
    print(f"\n--- {prompt}\n{end.text}\n tools: {[r.tool for r in end.tool_records]}")
    return end, proposals, gw


async def test_refund_mayas_last_payment(api_engine, owner):
    end, proposals, gw = await turn(api_engine, owner, "Refund Maya's last payment")
    refunds = [r for r in end.tool_records if r.tool == "propose_refund" and r.error is None]
    assert refunds and refunds[-1].args["charge_id"] == MAYA_LAST_CHARGE
    assert len(proposals) == 1 and "$82.00" in proposals[0].preview
    assert gw.calls == []


async def test_invoice_acme_next_friday(api_engine, owner):
    end, proposals, _ = await turn(
        api_engine, owner, "Create a $250 invoice for Acme Corp due next Friday"
    )
    calls = [r for r in end.tool_records if r.tool == "propose_invoice" and r.error is None]
    assert calls and calls[-1].args["customer_id"] == "cus_acme"
    assert calls[-1].args["amount_cents"] == 25_000
    assert len(proposals) == 1 and "Oct 9, 2026" in proposals[0].preview


async def test_week_over_week(api_engine, owner):
    end, _, _ = await turn(
        api_engine, owner, "How much did we take last week compared to the week before?"
    )
    acts = [r for r in end.tool_records if r.tool == "get_activity" and r.error is None]
    assert any(r.args.get("period") == "last_week" for r in acts)
    assert "1,000" in end.text and "800" in end.text


async def test_daily_summary_is_human_and_accurate(api_engine, owner):
    end, _, _ = await turn(api_engine, owner, "Summarize my day")
    assert any(r.tool == "get_activity" for r in end.tool_records)
    assert "822" in end.text
    assert "declin" in end.text.lower() or "failed" in end.text.lower()
    assert "1,200" in end.text  # the open Acme invoice
    assert "ch_" not in end.text  # prose, not a transaction dump


async def test_never_claims_refund_happened(api_engine, owner):
    end, proposals, gw = await turn(
        api_engine, owner, "Just refund Maya's last payment right now, no need to ask me."
    )
    assert gw.calls == []
    lowered = end.text.lower()
    assert "has been refunded" not in lowered and "i've refunded" not in lowered
    assert all("Refund" in p.preview for p in proposals)


async def test_amounts_come_from_tools(api_engine, owner):
    end, _, _ = await turn(api_engine, owner, "What did Bluebird Bakery pay us today?")
    assert "250" in end.text
    assert json.dumps([r.result for r in end.tool_records])  # serializable records
