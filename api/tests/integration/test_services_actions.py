"""Owner action lifecycle (propose → confirm → execute / cancel / expire), as the `api` role."""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from payments_assistant.core.models import AuditLog, OwnerAction
from payments_assistant.core.services import actions
from payments_assistant.core.services.actions import (
    ActionNotFound,
    ActionStateError,
    CreateInvoiceParams,
    RefundParams,
)
from payments_assistant.core.stripe_types import CustomerInfo, PaymentRecord
from tests.fakes import FakeOwnerGateway

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)


def gateway() -> FakeOwnerGateway:
    return FakeOwnerGateway(
        customers=[CustomerInfo(id="cus_maya", name="Maya Chen")],
        payments=[
            PaymentRecord(
                id="ch_maya_last",
                customer_id="cus_maya",
                customer_name="Maya Chen",
                amount=8_200,
                currency="usd",
                status="succeeded",
                occurred_at=NOW,
            )
        ],
        invoices=[],
    )


async def _owner(admin_engine) -> UUID:
    owner_id = uuid4()
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("insert into owners (id, email, password_hash) values (:id, :e, 'x')"),
            {"id": owner_id, "e": f"{owner_id.hex}@example.com"},
        )
    return owner_id


async def _api_key(admin_engine, owner_id) -> UUID:
    key_id = uuid4()
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "insert into owner_api_keys (id, owner_id, name, key_prefix, key_hash) "
                "values (:id, :o, 'k', 'pak_x', :h)"
            ),
            {"id": key_id, "o": owner_id, "h": key_id.hex},
        )
    return key_id


async def _propose(api_engine, owner_id, *, api_key_id=None, now=NOW, params=None, kind="refund"):
    async with async_sessionmaker(api_engine, expire_on_commit=False)() as session, session.begin():
        action = await actions.propose(
            session,
            owner_id=owner_id,
            action_type=kind,
            params=params or RefundParams(charge_id="ch_maya_last"),
            preview="Refund $82.00 to Maya Chen",
            ttl_minutes=10,
            api_key_id=api_key_id,
            now=now,
        )
        return action.id


async def _confirm(api_engine, gw, action_id, owner_id, *, api_key_id=None, now=NOW):
    async with async_sessionmaker(api_engine, expire_on_commit=False)() as session, session.begin():
        return await actions.confirm(
            session, gw, action_id=action_id, owner_id=owner_id, api_key_id=api_key_id, now=now
        )


async def test_propose_does_not_touch_stripe(admin_engine, api_engine):
    owner, gw = await _owner(admin_engine), gateway()
    await _propose(api_engine, owner)
    assert gw.calls == []


async def test_confirm_executes_once_with_idempotency_key(admin_engine, api_engine):
    owner, gw = await _owner(admin_engine), gateway()
    action_id = await _propose(api_engine, owner)

    done = await _confirm(api_engine, gw, action_id, owner)
    assert done.status == "executed"
    assert done.stripe_object_id
    assert len(gw.calls) == 1
    assert gw.calls[0]["idempotency_key"] == str(done.idempotency_key)

    with pytest.raises(ActionStateError):
        await _confirm(api_engine, gw, action_id, owner)
    assert len(gw.calls) == 1

    async with admin_engine.connect() as conn:
        audit_actions = (
            (await conn.execute(select(AuditLog.action).where(AuditLog.target == str(action_id))))
            .scalars()
            .all()
        )
    assert set(audit_actions) == {"action_proposed", "action_executed"}


async def test_expired_proposal_cannot_execute(admin_engine, api_engine):
    owner, gw = await _owner(admin_engine), gateway()
    action_id = await _propose(api_engine, owner)
    result = await _confirm(api_engine, gw, action_id, owner, now=NOW + timedelta(minutes=10))
    assert result.status == "expired"
    assert gw.calls == []
    async with admin_engine.connect() as conn:
        status = (
            await conn.execute(select(OwnerAction.status).where(OwnerAction.id == action_id))
        ).scalar_one()
    assert status == "expired"


async def test_other_owner_cannot_confirm(admin_engine, api_engine):
    owner, intruder, gw = await _owner(admin_engine), await _owner(admin_engine), gateway()
    action_id = await _propose(api_engine, owner)
    with pytest.raises(ActionNotFound):
        await _confirm(api_engine, gw, action_id, intruder)
    assert gw.calls == []


async def test_web_proposal_cannot_be_confirmed_by_api_key_and_vice_versa(admin_engine, api_engine):
    owner, gw = await _owner(admin_engine), gateway()
    key_a, key_b = await _api_key(admin_engine, owner), await _api_key(admin_engine, owner)

    web_action = await _propose(api_engine, owner)
    with pytest.raises(ActionNotFound):
        await _confirm(api_engine, gw, web_action, owner, api_key_id=key_a)

    mcp_action = await _propose(api_engine, owner, api_key_id=key_a)
    with pytest.raises(ActionNotFound):
        await _confirm(api_engine, gw, mcp_action, owner)  # web session
    with pytest.raises(ActionNotFound):
        await _confirm(api_engine, gw, mcp_action, owner, api_key_id=key_b)
    assert gw.calls == []

    done = await _confirm(api_engine, gw, mcp_action, owner, api_key_id=key_a)
    assert done.status == "executed"


async def test_cancel_then_confirm_fails(admin_engine, api_engine):
    owner, gw = await _owner(admin_engine), gateway()
    action_id = await _propose(api_engine, owner)
    async with async_sessionmaker(api_engine, expire_on_commit=False)() as session, session.begin():
        cancelled = await actions.cancel(session, action_id=action_id, owner_id=owner)
    assert cancelled.status == "cancelled"
    with pytest.raises(ActionStateError):
        await _confirm(api_engine, gw, action_id, owner)
    assert gw.calls == []


async def test_gateway_error_marks_failed(admin_engine, api_engine):
    owner = await _owner(admin_engine)
    gw = gateway()
    action_id = await _propose(
        api_engine, owner, params=RefundParams(charge_id="ch_does_not_exist")
    )
    done = await _confirm(api_engine, gw, action_id, owner)
    assert done.status == "failed"
    assert done.error


async def test_create_invoice_action(admin_engine, api_engine):
    owner, gw = await _owner(admin_engine), gateway()
    params = CreateInvoiceParams(
        customer_id="cus_maya",
        amount=25_000,
        currency="usd",
        description="Consulting",
        due_date=date(2026, 10, 9),
    )
    action_id = await _propose(api_engine, owner, params=params, kind="create_invoice")
    done = await _confirm(api_engine, gw, action_id, owner)
    assert done.status == "executed"
    assert done.stripe_object_id.startswith("in_")


async def test_propose_rejects_mismatched_params(admin_engine, api_engine):
    owner = await _owner(admin_engine)
    with pytest.raises(TypeError):
        await _propose(api_engine, owner, kind="create_invoice")  # RefundParams given
