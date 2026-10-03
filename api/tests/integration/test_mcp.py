"""Owner MCP server end to end: a real MCP client session against the in-process app."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import httpx2
import pytest
from asgi_lifespan import LifespanManager
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from payments_assistant.core.models import AuditLog, OwnerAction
from payments_assistant.core.services import api_keys
from payments_assistant.core.tools import tools_for
from payments_assistant.http.app import create_app
from tests.demo_data import MAYA_LAST_CHARGE
from tests.http_fixtures import create_owner

pytestmark = pytest.mark.integration


@pytest.fixture
async def app(harness) -> AsyncIterator:
    application = create_app(state=harness.state)
    async with LifespanManager(application) as manager:
        yield manager.app


async def _key(harness, owner_id, name="test") -> api_keys.NewKey:
    async with harness.state.sessionmaker() as s, s.begin():
        return await api_keys.create(s, owner_id=owner_id, name=name, now=harness.state.clock())


@pytest.fixture
async def owner_key(admin_engine, harness):
    owner_id, _ = await create_owner(admin_engine)
    return owner_id, await _key(harness, owner_id)


@asynccontextmanager
async def mcp_session(app, key: str) -> AsyncIterator[ClientSession]:
    client = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url="http://api",
        headers={"Authorization": f"Bearer {key}"},
    )
    async with client, streamable_http_client("http://api/mcp", http_client=client) as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def raw_post(app, headers: dict) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api"
    ) as c:
        return await c.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"accept": "application/json, text/event-stream", **headers},
        )


# ------------------------------------------------------------------------------------------ auth


@pytest.mark.parametrize(
    "headers",
    [{}, {"authorization": "Bearer pak_not_a_real_key"}, {"authorization": "Basic abc"}],
)
async def test_requires_a_valid_api_key(app, headers):
    r = await raw_post(app, headers)
    assert r.status_code == 401
    assert "Bearer" in r.headers["www-authenticate"]


async def test_session_token_is_rejected_on_mcp(app, owner):
    r = await raw_post(app, {"authorization": f"Bearer {owner.token}"})
    assert r.status_code == 401


async def test_api_key_is_rejected_on_web_api(app, owner_key):
    _, key = owner_key
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api"
    ) as c:
        r = await c.get("/auth/me", headers={"authorization": f"Bearer {key.key}"})
    assert r.status_code == 401


async def test_revoked_key_is_rejected(app, owner_key, harness):
    owner_id, key = owner_key
    async with harness.state.sessionmaker() as s, s.begin():
        assert await api_keys.revoke(s, owner_id=owner_id, key_id=key.id, now=harness.state.clock())
    assert (await raw_post(app, {"authorization": f"Bearer {key.key}"})).status_code == 401


# ------------------------------------------------------------------------------------------ tools


async def test_lists_owner_tools_only_with_registry_schemas(app, owner_key):
    _, key = owner_key
    async with mcp_session(app, key.key) as s:
        tools = {t.name: t for t in (await s.list_tools()).tools}
    owner = {t.name: t for t in tools_for("owner")}
    assert set(tools) == set(owner) | {"confirm_action", "cancel_action"}
    assert not {t.name for t in tools_for("customer")} & set(tools)
    for name, spec in owner.items():
        assert tools[name].input_schema == spec.json_schema()["function"]["parameters"]
    assert tools["confirm_action"].annotations.destructive_hint is True
    assert tools["get_activity"].annotations.read_only_hint is True


async def test_read_tool_returns_structured_result(app, owner_key):
    _, key = owner_key
    async with mcp_session(app, key.key) as s:
        r = await s.call_tool("get_activity", {"period": "today"})
    assert not r.is_error
    assert r.structured_content["current"]["gross"] == "$822.00"


async def test_tool_errors_are_reported_not_raised(app, owner_key):
    _, key = owner_key
    async with mcp_session(app, key.key) as s:
        bad = await s.call_tool("find_payments", {"customer_query": "Zebra Ltd"})
        invalid = await s.call_tool("propose_refund", {})
        unknown = await s.call_tool("drop_tables", {})
    assert bad.is_error and "No customer matches" in bad.content[0].text
    assert invalid.is_error and "Invalid arguments" in invalid.content[0].text
    assert unknown.is_error


async def test_propose_then_confirm_executes_once(app, owner_key, harness):
    _, key = owner_key
    async with mcp_session(app, key.key) as s:
        proposal = await s.call_tool("propose_refund", {"charge_id": MAYA_LAST_CHARGE})
        assert harness.gateway.calls == []  # proposing moves no money
        action_id = proposal.structured_content["action_id"]
        done = await s.call_tool("confirm_action", {"action_id": action_id})
        again = await s.call_tool("confirm_action", {"action_id": action_id})
    assert done.structured_content["status"] == "executed"
    assert done.structured_content["stripe_object_id"].startswith("re_")
    assert again.is_error and "already executed" in again.content[0].text
    assert [c["method"] for c in harness.gateway.calls] == ["refund"]

    async with async_sessionmaker(harness.state.engine)() as db:
        action = await db.get(OwnerAction, __import__("uuid").UUID(action_id))
        assert action.api_key_id == key.id
        actors = (
            (await db.execute(select(AuditLog.actor_type).where(AuditLog.target == action_id)))
            .scalars()
            .all()
        )
    assert set(actors) == {"owner_mcp"}


async def test_other_key_cannot_confirm_and_web_cannot_either(
    app, owner_key, harness, owner_client
):
    owner_id, key = owner_key
    other = await _key(harness, owner_id, "second")
    async with mcp_session(app, key.key) as s:
        action_id = (
            await s.call_tool("propose_refund", {"charge_id": MAYA_LAST_CHARGE})
        ).structured_content["action_id"]
    async with mcp_session(app, other.key) as s:
        r = await s.call_tool("confirm_action", {"action_id": action_id})
    assert r.is_error and "No such pending action" in r.content[0].text
    assert harness.gateway.calls == []


async def test_expired_proposal_reports_expired(app, owner_key, harness):
    from datetime import timedelta

    _, key = owner_key
    async with mcp_session(app, key.key) as s:
        action_id = (
            await s.call_tool("propose_refund", {"charge_id": MAYA_LAST_CHARGE})
        ).structured_content["action_id"]
        start = harness.state.clock()
        harness.state.clock = lambda: start + timedelta(minutes=11)
        r = await s.call_tool("confirm_action", {"action_id": action_id})
    assert r.is_error and r.structured_content["status"] == "expired"
    assert harness.gateway.calls == []


async def test_cancel_action(app, owner_key, harness):
    _, key = owner_key
    async with mcp_session(app, key.key) as s:
        action_id = (
            await s.call_tool("propose_refund", {"charge_id": MAYA_LAST_CHARGE})
        ).structured_content["action_id"]
        r = await s.call_tool("cancel_action", {"action_id": action_id})
        after = await s.call_tool("confirm_action", {"action_id": action_id})
    assert r.structured_content["status"] == "cancelled"
    assert after.is_error
    assert harness.gateway.calls == []
