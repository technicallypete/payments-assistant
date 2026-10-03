"""Owner login / session / logout over HTTP (spec §3.1)."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from payments_assistant.core.models import LoginAttempt, OwnerSession
from payments_assistant.core.security import hash_token
from tests.http_fixtures import create_owner

pytestmark = pytest.mark.integration


async def test_login_me_logout_roundtrip(client, admin_engine):
    _, email = await create_owner(admin_engine, "pw-123456")
    r = await client.post("/auth/login", json={"email": email.upper(), "password": "pw-123456"})
    assert r.status_code == 200
    body = r.json()
    token = body["token"]
    assert body["owner"]["email"] == email
    assert len(token) >= 40

    auth = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/auth/me", headers=auth)).json()["email"] == email

    assert (await client.post("/auth/logout", headers=auth)).status_code == 204
    assert (await client.get("/auth/me", headers=auth)).status_code == 401


async def test_only_token_hash_is_stored(client, admin_engine, harness):
    _, email = await create_owner(admin_engine, "pw-123456")
    token = (
        await client.post("/auth/login", json={"email": email, "password": "pw-123456"})
    ).json()["token"]
    async with harness.state.sessionmaker() as s:
        rows = (await s.execute(select(OwnerSession.token_hash))).scalars().all()
    assert hash_token(token) in rows
    assert token not in rows


async def test_wrong_password_and_unknown_email_look_the_same(client, admin_engine):
    _, email = await create_owner(admin_engine, "pw-123456")
    bad = await client.post("/auth/login", json={"email": email, "password": "nope"})
    unknown = await client.post("/auth/login", json={"email": "who@x.com", "password": "nope"})
    assert bad.status_code == unknown.status_code == 401
    assert bad.json() == unknown.json()


async def test_throttle_after_repeated_failures(client, admin_engine, harness):
    _, email = await create_owner(admin_engine, "pw-123456")
    limit = harness.state.settings.login_max_attempts
    for _ in range(limit):
        assert (
            await client.post("/auth/login", json={"email": email, "password": "x"})
        ).status_code == 401
    # Even the right password is refused while throttled.
    r = await client.post("/auth/login", json={"email": email, "password": "pw-123456"})
    assert r.status_code == 429
    async with harness.state.sessionmaker() as s:
        n = len((await s.execute(select(LoginAttempt).where(LoginAttempt.email == email))).all())
    assert n == limit


async def test_throttle_window_expires(client, admin_engine, harness):
    _, email = await create_owner(admin_engine, "pw-123456")
    for _ in range(harness.state.settings.login_max_attempts):
        await client.post("/auth/login", json={"email": email, "password": "x"})
    start = harness.state.clock()
    harness.state.clock = lambda: start + timedelta(minutes=16)
    r = await client.post("/auth/login", json={"email": email, "password": "pw-123456"})
    assert r.status_code == 200


@pytest.mark.parametrize(
    "header",
    [None, "Bearer", "Bearer nonsense-token", "Basic abc", "Bearer pak_live_looking_api_key"],
)
async def test_protected_routes_reject_bad_credentials(client, header):
    headers = {"Authorization": header} if header else {}
    for path in ("/auth/me", "/conversations"):
        assert (await client.get(path, headers=headers)).status_code == 401


async def test_expired_session_rejected_and_activity_slides_expiry(client, owner, harness):
    auth = {"Authorization": f"Bearer {owner.token}"}
    start = harness.state.clock()
    ttl = timedelta(days=harness.state.settings.session_ttl_days)

    harness.state.clock = lambda: start + ttl - timedelta(hours=1)  # still valid; slides expiry
    assert (await client.get("/auth/me", headers=auth)).status_code == 200
    harness.state.clock = lambda: start + ttl + timedelta(hours=1)  # past ORIGINAL expiry
    assert (await client.get("/auth/me", headers=auth)).status_code == 200
    harness.state.clock = lambda: start + 3 * ttl
    assert (await client.get("/auth/me", headers=auth)).status_code == 401
