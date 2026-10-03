"""Owner customer list, Telegram invites, and Stripe sync over HTTP."""

import hashlib
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


async def _account_id(admin_engine, stripe_id: str) -> UUID:
    async with admin_engine.connect() as conn:
        return (
            await conn.execute(
                text("select id from customer_accounts where stripe_customer_id = :c"),
                {"c": stripe_id},
            )
        ).scalar_one()


async def test_requires_owner_session(client):
    assert (await client.get("/customers")).status_code == 401
    r = await client.post("/customers/sync", headers={"Authorization": "Bearer pak_abc"})
    assert r.status_code == 401


async def test_sync_creates_demo_accounts_and_is_idempotent(admin_engine, owner_client):
    first = await owner_client.post("/customers/sync")
    assert first.status_code == 200 and first.json() == {"synced": 3}
    second = await owner_client.post("/customers/sync")
    assert second.json() == {"synced": 3}
    async with admin_engine.connect() as conn:
        n = (
            await conn.execute(
                text(
                    "select count(*) from customer_accounts "
                    "where stripe_customer_id in ('cus_maya', 'cus_acme', 'cus_blue')"
                )
            )
        ).scalar_one()
    assert n == 3


async def test_list_shows_amount_owed_and_telegram_link(admin_engine, owner_client):
    await owner_client.post("/customers/sync")
    maya = await _account_id(admin_engine, "cus_maya")
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "insert into telegram_identities "
                "(customer_account_id, telegram_user_id, telegram_chat_id) values (:a, :u, :u)"
            ),
            {"a": maya, "u": uuid4().int % 9_000_000_000},
        )

    rows = {r["stripe_customer_id"]: r for r in (await owner_client.get("/customers")).json()}
    assert rows["cus_acme"]["amount_owed"] == "$1,200.00"
    assert rows["cus_acme"]["amount_owed_cents"] == 120_000
    assert rows["cus_blue"]["amount_owed"] == "$0.00"
    assert rows["cus_maya"]["telegram_linked"] is True
    assert rows["cus_acme"]["telegram_linked"] is False
    assert rows["cus_acme"]["display_name"] == "Acme Corp"


async def test_invite_url_and_only_hash_stored(admin_engine, owner_client, harness, owner):
    harness.state.settings.telegram_bot_username = "test_payments_bot"
    await owner_client.post("/customers/sync")
    acme = await _account_id(admin_engine, "cus_acme")

    r = await owner_client.post(f"/customers/{acme}/invite")
    assert r.status_code == 200
    url = urlparse(r.json()["url"])
    assert (url.scheme, url.netloc, url.path) == ("https", "t.me", "/test_payments_bot")
    token = parse_qs(url.query)["start"][0]
    assert len(token) == 32

    async with admin_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "select token_hash, created_by from customer_invites "
                    "where customer_account_id = :a"
                ),
                {"a": acme},
            )
        ).all()
        leaked = (
            await conn.execute(
                text("select count(*) from customer_invites where token_hash = :t"), {"t": token}
            )
        ).scalar_one()
    assert hashlib.sha256(token.encode()).hexdigest() in {h for h, _ in rows}
    assert leaked == 0
    assert owner.owner_id in {c for _, c in rows}


async def test_invite_for_unknown_account_is_404(owner_client, harness):
    harness.state.settings.telegram_bot_username = "test_payments_bot"
    assert (await owner_client.post(f"/customers/{uuid4()}/invite")).status_code == 404


async def test_invite_without_bot_username_is_503(admin_engine, owner_client, harness):
    harness.state.settings.telegram_bot_username = ""
    await owner_client.post("/customers/sync")
    acme = await _account_id(admin_engine, "cus_acme")
    assert (await owner_client.post(f"/customers/{acme}/invite")).status_code == 503
