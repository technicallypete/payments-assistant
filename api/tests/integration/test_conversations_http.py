"""Owner chat over HTTP: conversations + the SSE message stream (spec §6.1)."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from payments_assistant.core.models import Message, OwnerAction
from payments_assistant.http.routes.conversations import _stream_turn
from tests.demo_data import MAYA_LAST_CHARGE
from tests.fake_llm import reply
from tests.http_fixtures import create_owner, parse_sse

pytestmark = pytest.mark.integration


async def _new_conversation(owner_client) -> str:
    r = await owner_client.post("/conversations", json={})
    assert r.status_code == 201
    return r.json()["id"]


async def test_stream_event_order_and_persistence(owner_client, harness):
    conv = await _new_conversation(owner_client)
    harness.set_script(
        reply("Let me check.", ("find_payments", {"customer_query": "Maya", "limit": 1})),
        reply("", ("propose_refund", {"charge_id": MAYA_LAST_CHARGE})),
        reply("Ready: confirm the $82.00 refund."),
    )
    r = await owner_client.post(
        f"/conversations/{conv}/messages", json={"content": "Refund Maya's last payment"}
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    types = [e["type"] for e in events]

    assert types[0] == "message_start" and types[-1] == "message_end"
    assert types.index("tool_start") < types.index("tool_end") < types.index("action_proposed")
    assert "token" in types
    start, end = events[0], events[-1]
    assert start["message_id"] == end["message_id"]
    proposal = next(e for e in events if e["type"] == "action_proposed")
    assert proposal["preview"].startswith("Refund $82.00 to Maya Chen")

    detail = (await owner_client.get(f"/conversations/{conv}")).json()
    roles = [m["role"] for m in detail["messages"]]
    assert roles == ["user", "tool", "tool", "assistant"]
    assistant = detail["messages"][-1]
    assert assistant["id"] == start["message_id"]
    assert assistant["content"] == "Let me check.Ready: confirm the $82.00 refund."
    assert assistant["status"] == "complete"
    assert detail["title"] == "Refund Maya's last payment"

    async with harness.state.sessionmaker() as s:
        action = await s.get(OwnerAction, UUID(proposal["action_id"]))
    assert action.status == "proposed"  # committed with the turn


async def test_history_is_sent_on_the_next_turn(owner_client, harness):
    conv = await _new_conversation(owner_client)
    harness.set_script(reply("First answer"))
    await owner_client.post(f"/conversations/{conv}/messages", json={"content": "first"})
    harness.set_script(reply("Second answer"))
    await owner_client.post(f"/conversations/{conv}/messages", json={"content": "second"})
    seen = harness.models[-1].seen[0]
    assert [m.content for m in seen[1:]] == ["first", "First answer", "second"]


async def test_provider_error_is_streamed_and_persisted_as_error(owner_client, harness):
    conv = await _new_conversation(owner_client)
    harness.set_script()  # empty script: the model raises on its first call
    r = await owner_client.post(f"/conversations/{conv}/messages", json={"content": "hi"})
    types = [e["type"] for e in parse_sse(r.text)]
    assert "error" in types and types[-1] == "message_end"
    detail = (await owner_client.get(f"/conversations/{conv}")).json()
    assert detail["messages"][-1]["status"] == "error"


async def test_client_disconnect_persists_interrupted_partial(owner, harness):
    """Simulate the browser going away mid-stream: close the generator after the first token."""
    async with harness.state.sessionmaker() as s, s.begin():
        from payments_assistant.core.services import conversations as convs

        conv = await convs.create_owner_conversation(
            s, owner_id=owner.owner_id, now=harness.state.clock()
        )
        await convs.append_message(s, conv, role="user", content="hi", now=harness.state.clock())
    harness.set_script(reply("Partial answer that never finishes"))

    gen = _stream_turn(harness.state, owner.owner_id, conv.id, [])
    frames = [await gen.__anext__(), await gen.__anext__()]  # message_start + first token
    assert b"message_start" in frames[0] and b"token" in frames[1]
    await gen.aclose()

    async with harness.state.sessionmaker() as s:
        msgs = (
            (
                await s.execute(
                    select(Message).where(Message.conversation_id == conv.id).order_by(Message.seq)
                )
            )
            .scalars()
            .all()
        )
    assistant = msgs[-1]
    assert assistant.role == "assistant"
    assert assistant.status == "interrupted"
    assert assistant.content == "Partial"


async def test_conversations_are_private_to_their_owner(
    owner_client, client, admin_engine, harness
):
    conv = await _new_conversation(owner_client)
    _, other_email = await create_owner(admin_engine, "other-pass")
    token = (
        await client.post("/auth/login", json={"email": other_email, "password": "other-pass"})
    ).json()["token"]
    other = {"Authorization": f"Bearer {token}"}
    assert (await client.get(f"/conversations/{conv}", headers=other)).status_code == 404
    r = await client.post(f"/conversations/{conv}/messages", json={"content": "x"}, headers=other)
    assert r.status_code == 404
    assert conv not in [c["id"] for c in (await client.get("/conversations", headers=other)).json()]


async def test_unknown_conversation_404(owner_client):
    r = await owner_client.post(f"/conversations/{uuid4()}/messages", json={"content": "x"})
    assert r.status_code == 404


async def test_empty_message_rejected(owner_client):
    conv = await _new_conversation(owner_client)
    assert (
        await owner_client.post(f"/conversations/{conv}/messages", json={"content": ""})
    ).status_code == 422
