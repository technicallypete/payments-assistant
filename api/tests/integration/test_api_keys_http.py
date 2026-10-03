"""Owner API keys over HTTP: shown once, hashed at rest, owner-scoped, session-auth only."""

import hashlib
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from payments_assistant.core.models import AuditLog, OwnerApiKey
from tests.http_fixtures import create_owner

pytestmark = pytest.mark.integration


async def _create(owner_client, name="Claude Desktop") -> dict:
    r = await owner_client.post("/api-keys", json={"name": name})
    assert r.status_code == 201
    return r.json()


async def test_create_returns_key_once_with_client_config(owner_client):
    body = await _create(owner_client)
    key = body["key"]
    assert key.startswith("pak_") and len(key) > 40
    assert body["display_prefix"] == key[:12]
    assert body["mcp_url"].endswith("/mcp")
    assert f"Authorization: Bearer {key}" in body["config"]["claude_code"]
    assert "claude mcp add --transport http penny" in body["config"]["claude_code"]
    assert "mcp-remote" in body["config"]["claude_desktop"]
    assert key in body["config"]["claude_desktop"]

    listed = (await owner_client.get("/api-keys")).json()
    assert [k["id"] for k in listed] == [body["id"]]
    assert "key" not in listed[0] and "key_hash" not in listed[0]
    assert key not in str(listed)


async def test_only_sha256_is_stored(owner_client, admin_engine):
    body = await _create(owner_client)
    async with async_sessionmaker(admin_engine)() as s:
        row = await s.get(OwnerApiKey, body["id"])
    assert row.key_hash == hashlib.sha256(body["key"].encode()).hexdigest()
    assert body["key"] not in (row.key_hash, row.key_prefix, row.name)


async def test_revoke_and_audit(owner_client, admin_engine):
    body = await _create(owner_client)
    assert (await owner_client.delete(f"/api-keys/{body['id']}")).status_code == 204
    listed = (await owner_client.get("/api-keys")).json()
    assert listed[0]["revoked_at"] is not None
    assert (await owner_client.delete(f"/api-keys/{body['id']}")).status_code == 404
    async with async_sessionmaker(admin_engine)() as s:
        actions = (
            (await s.execute(select(AuditLog.action).where(AuditLog.target == body["id"])))
            .scalars()
            .all()
        )
    assert set(actions) == {"api_key_created", "api_key_revoked"}


async def test_unknown_key_404(owner_client):
    assert (await owner_client.delete(f"/api-keys/{uuid4()}")).status_code == 404


async def test_other_owner_cannot_see_or_revoke(owner_client, client, admin_engine):
    body = await _create(owner_client)
    _, email = await create_owner(admin_engine, "other-pw")
    token = (
        await client.post("/auth/login", json={"email": email, "password": "other-pw"})
    ).json()["token"]
    other = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/api-keys", headers=other)).json() == []
    assert (await client.delete(f"/api-keys/{body['id']}", headers=other)).status_code == 404


async def test_api_key_cannot_manage_keys(owner_client, client):
    """Keys only work on /mcp; they can't be used to mint or list more keys."""
    key = (await _create(owner_client))["key"]
    bearer = {"Authorization": f"Bearer {key}"}
    assert (await client.get("/api-keys", headers=bearer)).status_code == 401
    r = await client.post("/api-keys", json={"name": "x"}, headers=bearer)
    assert r.status_code == 401


async def test_requires_auth(client):
    assert (await client.get("/api-keys")).status_code == 401


async def test_name_validation(owner_client):
    assert (await owner_client.post("/api-keys", json={"name": ""})).status_code == 422
