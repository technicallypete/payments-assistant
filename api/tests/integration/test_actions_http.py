"""Confirm / cancel over HTTP: the only path that moves money."""

from datetime import timedelta
from uuid import uuid4

import pytest

from tests.demo_data import MAYA_LAST_CHARGE
from tests.fake_llm import reply
from tests.http_fixtures import create_owner, parse_sse

pytestmark = pytest.mark.integration


async def _propose_refund(owner_client, harness) -> str:
    conv = (await owner_client.post("/conversations", json={})).json()["id"]
    harness.set_script(
        reply("", ("propose_refund", {"charge_id": MAYA_LAST_CHARGE})), reply("Confirm?")
    )
    r = await owner_client.post(f"/conversations/{conv}/messages", json={"content": "refund maya"})
    return next(e for e in parse_sse(r.text) if e["type"] == "action_proposed")["action_id"]


async def test_confirm_executes_once(owner_client, harness):
    action_id = await _propose_refund(owner_client, harness)
    pending = (await owner_client.get("/actions")).json()
    assert [a["id"] for a in pending] == [action_id]

    r = await owner_client.post(f"/actions/{action_id}/confirm")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "executed" and body["stripe_object_id"].startswith("re_")
    assert [c["method"] for c in harness.gateway.calls] == ["refund"]

    again = await owner_client.post(f"/actions/{action_id}/confirm")
    assert again.status_code == 409
    assert len(harness.gateway.calls) == 1
    assert (await owner_client.get("/actions")).json() == []


async def test_cancel_then_confirm_conflicts(owner_client, harness):
    action_id = await _propose_refund(owner_client, harness)
    assert (await owner_client.post(f"/actions/{action_id}/cancel")).json()["status"] == "cancelled"
    assert (await owner_client.post(f"/actions/{action_id}/confirm")).status_code == 409
    assert harness.gateway.calls == []


async def test_expired_proposal_reports_expired(owner_client, harness):
    action_id = await _propose_refund(owner_client, harness)
    start = harness.state.clock()
    harness.state.clock = lambda: start + timedelta(minutes=11)
    r = await owner_client.post(f"/actions/{action_id}/confirm")
    assert r.status_code == 200 and r.json()["status"] == "expired"
    assert harness.gateway.calls == []


async def test_other_owner_cannot_confirm(owner_client, client, admin_engine, harness):
    action_id = await _propose_refund(owner_client, harness)
    _, email = await create_owner(admin_engine, "pw-other")
    token = (
        await client.post("/auth/login", json={"email": email, "password": "pw-other"})
    ).json()["token"]
    r = await client.post(
        f"/actions/{action_id}/confirm", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 404
    assert harness.gateway.calls == []


async def test_unknown_action_404(owner_client):
    assert (await owner_client.post(f"/actions/{uuid4()}/confirm")).status_code == 404


async def test_expired_proposals_drop_out_of_pending_list(owner_client, harness):
    action_id = await _propose_refund(owner_client, harness)
    assert [a["id"] for a in (await owner_client.get("/actions")).json()] == [action_id]
    start = harness.state.clock()
    harness.state.clock = lambda: start + timedelta(minutes=11)
    assert (await owner_client.get("/actions")).json() == []
