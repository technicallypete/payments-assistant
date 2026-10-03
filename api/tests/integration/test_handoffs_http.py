"""Owner handoff queue over HTTP."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from tests.demo_data import NOW

pytestmark = pytest.mark.integration


async def _account(admin_engine, name="Jordan Lee") -> UUID:
    account_id = uuid4()
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "insert into customer_accounts (id, stripe_customer_id, display_name) "
                "values (:id, :cus, :n)"
            ),
            {"id": account_id, "cus": f"cus_{account_id.hex[:12]}", "n": name},
        )
    return account_id


async def _handoff(admin_engine, account_id, *, status="open", minutes_ago=0, amount=200_000):
    handoff_id = uuid4()
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "insert into handoffs (id, customer_account_id, reason, stripe_invoice_id, amount, "
                "currency, summary, status, created_at) values (:id, :a, 'amount_over_threshold', "
                ":inv, :amt, 'usd', 'Wants to pay a big invoice', :st, :ts)"
            ),
            {
                "id": handoff_id,
                "a": account_id,
                "inv": f"in_{handoff_id.hex[:8]}",
                "amt": amount,
                "st": status,
                "ts": NOW - timedelta(minutes=minutes_ago),
            },
        )
    return handoff_id


async def test_requires_owner_session(client):
    assert (await client.get("/handoffs")).status_code == 401
    r = await client.get("/handoffs", headers={"Authorization": "Bearer pak_whatever"})
    assert r.status_code == 401


async def test_list_open_newest_first_with_details(admin_engine, owner_client):
    account = await _account(admin_engine)
    older = await _handoff(admin_engine, account, minutes_ago=10)
    newer = await _handoff(admin_engine, account, minutes_ago=1, amount=350_000)
    done = await _handoff(admin_engine, account, status="resolved")

    rows = (await owner_client.get("/handoffs")).json()
    ids = [r["id"] for r in rows]
    assert ids.index(str(newer)) < ids.index(str(older))
    assert str(done) not in ids
    mine = next(r for r in rows if r["id"] == str(newer))
    assert mine["customer"] == "Jordan Lee"
    assert mine["amount"] == "$3,500.00"
    assert mine["reason"] == "amount_over_threshold"
    assert mine["stripe_invoice_id"].startswith("in_")

    all_ids = [r["id"] for r in (await owner_client.get("/handoffs?status=all")).json()]
    assert {str(older), str(newer), str(done)} <= set(all_ids)
    resolved = [r["id"] for r in (await owner_client.get("/handoffs?status=resolved")).json()]
    assert str(done) in resolved and str(newer) not in resolved


async def test_invalid_status_filter_is_422(owner_client):
    assert (await owner_client.get("/handoffs?status=bogus")).status_code == 422


async def test_acknowledge_then_resolve_writes_audit(admin_engine, owner_client, owner):
    account = await _account(admin_engine)
    handoff = await _handoff(admin_engine, account)

    r = await owner_client.post(f"/handoffs/{handoff}/acknowledge")
    assert r.status_code == 200 and r.json()["status"] == "acknowledged"

    r = await owner_client.post(f"/handoffs/{handoff}/resolve")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "resolved"
    assert body["resolved_at"].startswith("2026-10-03")

    async with admin_engine.connect() as conn:
        resolved_by = (
            await conn.execute(
                text("select resolved_by from handoffs where id = :id"), {"id": handoff}
            )
        ).scalar_one()
        actions = (
            (
                await conn.execute(
                    text("select action from audit_log where target = :t order by created_at"),
                    {"t": str(handoff)},
                )
            )
            .scalars()
            .all()
        )
    assert resolved_by == owner.owner_id
    assert set(actions) == {"handoff_acknowledged", "handoff_resolved"}


async def test_resolving_twice_is_409(admin_engine, owner_client):
    account = await _account(admin_engine)
    handoff = await _handoff(admin_engine, account)
    assert (await owner_client.post(f"/handoffs/{handoff}/resolve")).status_code == 200
    assert (await owner_client.post(f"/handoffs/{handoff}/resolve")).status_code == 409
    assert (await owner_client.post(f"/handoffs/{handoff}/acknowledge")).status_code == 409


@pytest.mark.parametrize("action", ["acknowledge", "resolve"])
async def test_unknown_handoff_is_404(owner_client, action):
    assert (await owner_client.post(f"/handoffs/{uuid4()}/{action}")).status_code == 404
