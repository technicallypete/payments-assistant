"""Models match the migrated schema, and the key table constraints hold."""

from uuid import uuid4

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from payments_assistant.core.models import Base

pytestmark = pytest.mark.integration


async def test_every_mapped_column_exists(admin_engine):
    async with admin_engine.connect() as conn:

        def _columns(sync_conn):
            insp = inspect(sync_conn)
            return {t: {c["name"] for c in insp.get_columns(t)} for t in insp.get_table_names()}

        db = await conn.run_sync(_columns)

    for table in Base.metadata.sorted_tables:
        assert table.name in db, f"table {table.name} missing from migrations"
        mapped = {c.name for c in table.columns}
        assert mapped == db[table.name], f"{table.name}: model {mapped ^ db[table.name]} differ"


async def _account(conn):
    account_id = uuid4()
    await conn.execute(
        text(
            "insert into customer_accounts (id, stripe_customer_id, display_name) "
            "values (:id, :cus, 'C')"
        ),
        {"id": account_id, "cus": f"cus_{account_id.hex[:12]}"},
    )
    return account_id


async def _owner(conn):
    owner_id = uuid4()
    await conn.execute(
        text("insert into owners (id, email, password_hash) values (:id, :e, 'x')"),
        {"id": owner_id, "e": f"{owner_id.hex}@example.com"},
    )
    return owner_id


@pytest.mark.parametrize(
    "channel,with_customer,with_owner",
    [
        ("customer_telegram", False, False),
        ("customer_telegram", True, True),
        ("customer_telegram", False, True),
        ("owner_web", True, False),
        ("owner_web", True, True),
    ],
)
async def test_conversation_actor_must_match_channel(
    admin_engine, channel, with_customer, with_owner
):
    with pytest.raises(IntegrityError, match="conversations_actor_matches_channel"):
        async with admin_engine.begin() as conn:
            customer = await _account(conn) if with_customer else None
            owner = await _owner(conn) if with_owner else None
            await conn.execute(
                text(
                    "insert into conversations (channel, customer_account_id, owner_id) "
                    "values (:ch, :c, :o)"
                ),
                {"ch": channel, "c": customer, "o": owner},
            )


async def test_one_open_payment_request_per_invoice(admin_engine):
    invoice = f"in_{uuid4().hex[:12]}"
    insert = text(
        "insert into payment_requests "
        "(customer_account_id, stripe_invoice_id, amount, currency, hosted_url, status) "
        "values (:a, :inv, 1000, 'usd', 'https://x', :st)"
    )
    async with admin_engine.begin() as conn:
        account = await _account(conn)
        await conn.execute(insert, {"a": account, "inv": invoice, "st": "failed"})
        await conn.execute(insert, {"a": account, "inv": invoice, "st": "link_sent"})

    with pytest.raises(IntegrityError, match="payment_requests_open_invoice_key"):
        async with admin_engine.begin() as conn:
            await conn.execute(insert, {"a": account, "inv": invoice, "st": "link_sent"})


async def test_one_active_telegram_link_per_user(admin_engine):
    tg = uuid4().int % 9_000_000_000
    insert = text(
        "insert into telegram_identities "
        "(customer_account_id, telegram_user_id, telegram_chat_id, revoked_at) "
        "values (:a, :u, :u, :rev)"
    )
    async with admin_engine.begin() as conn:
        account = await _account(conn)
        await conn.execute(insert, {"a": account, "u": tg, "rev": None})

    with pytest.raises(IntegrityError, match="telegram_identities_active_user_key"):
        async with admin_engine.begin() as conn:
            await conn.execute(insert, {"a": account, "u": tg, "rev": None})


async def test_message_customer_must_match_conversation(admin_engine):
    async with admin_engine.begin() as conn:
        account = await _account(conn)
        conv = uuid4()
        await conn.execute(
            text(
                "insert into conversations (id, channel, customer_account_id) "
                "values (:id, 'customer_telegram', :a)"
            ),
            {"id": conv, "a": account},
        )

    with pytest.raises(IntegrityError, match="must match its conversation"):
        async with admin_engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into messages (conversation_id, customer_account_id, role, content) "
                    "values (:c, null, 'user', 'hi')"
                ),
                {"c": conv},
            )
