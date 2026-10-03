"""RLS and grant matrix (spec §4, §5.4, §8). Security-load-bearing: never weaken these.

All customer-data assertions run as the `bot` or `api` runtime roles. `admin` is used only to seed,
because a superuser bypasses RLS. Each test seeds its own uniquely-identified customers, so rows
left behind by other tests can't satisfy (or break) an assertion.

S608 is disabled file-wide: table names interpolated into SQL come only from the constant lists
below, never from input.
"""

# ruff: noqa: S608

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from payments_assistant.core.scoping import customer_scope
from tests.integration.conftest import role_url

pytestmark = pytest.mark.integration

# Table -> column that RLS keys on.
CUSTOMER_TABLES = {
    "customer_accounts": "id",
    "telegram_identities": "customer_account_id",
    "conversations": "customer_account_id",
    "messages": "customer_account_id",
    "payment_requests": "customer_account_id",
    "handoffs": "customer_account_id",
}
OWNER_ONLY_TABLES = [
    "customer_invites",
    "owner_actions",
    "daily_summaries",
    "owners",
    "owner_sessions",
    "owner_api_keys",
    "stripe_events",
    "login_attempts",
]


@dataclass
class Customer:
    account_id: UUID
    rows: dict[str, set[UUID]] = field(default_factory=dict)

    @property
    def conversation_id(self) -> UUID:
        return next(iter(self.rows["conversations"]))


@dataclass
class Seed:
    a: Customer
    b: Customer
    owner_id: UUID
    owner_conversation_id: UUID


async def _seed_customer(conn, tg_user_id: int) -> Customer:
    acct, ident, conv, msg, pay, hand = (uuid4() for _ in range(6))
    await conn.execute(
        text(
            "INSERT INTO customer_accounts (id, stripe_customer_id, display_name) "
            "VALUES (:id, :cus, 'Test Customer')"
        ),
        {"id": acct, "cus": f"cus_{acct.hex}"},
    )
    await conn.execute(
        text(
            "INSERT INTO telegram_identities "
            "(id, customer_account_id, telegram_user_id, telegram_chat_id) "
            "VALUES (:id, :acct, :tg, :tg)"
        ),
        {"id": ident, "acct": acct, "tg": tg_user_id},
    )
    await conn.execute(
        text(
            "INSERT INTO conversations (id, channel, customer_account_id) "
            "VALUES (:id, 'customer_telegram', :acct)"
        ),
        {"id": conv, "acct": acct},
    )
    await conn.execute(
        text(
            "INSERT INTO messages (id, conversation_id, customer_account_id, role, content) "
            "VALUES (:id, :conv, :acct, 'user', 'what do I owe?')"
        ),
        {"id": msg, "conv": conv, "acct": acct},
    )
    await conn.execute(
        text(
            "INSERT INTO payment_requests "
            "(id, customer_account_id, conversation_id, stripe_invoice_id, amount, currency, "
            "hosted_url) "
            "VALUES (:id, :acct, :conv, :inv, 12000, 'usd', 'https://invoice.stripe.com/x')"
        ),
        {"id": pay, "acct": acct, "conv": conv, "inv": f"in_{pay.hex}"},
    )
    await conn.execute(
        text(
            "INSERT INTO handoffs "
            "(id, customer_account_id, conversation_id, reason, amount, currency) "
            "VALUES (:id, :acct, :conv, 'amount_over_threshold', 250000, 'usd')"
        ),
        {"id": hand, "acct": acct, "conv": conv},
    )
    return Customer(
        account_id=acct,
        rows={
            "customer_accounts": {acct},
            "telegram_identities": {ident},
            "conversations": {conv},
            "messages": {msg},
            "payment_requests": {pay},
            "handoffs": {hand},
        },
    )


def _tg_id() -> int:
    return uuid4().int % 10**12


@pytest.fixture
async def seed(admin_engine: AsyncEngine) -> Seed:
    """Two customers with a row in every customer-scoped table, plus an owner conversation."""
    owner_id, owner_conv = uuid4(), uuid4()
    async with admin_engine.begin() as conn:
        a = await _seed_customer(conn, _tg_id())
        b = await _seed_customer(conn, _tg_id())
        await conn.execute(
            text("INSERT INTO owners (id, email, password_hash) VALUES (:id, :email, 'x')"),
            {"id": owner_id, "email": f"owner-{owner_id.hex}@example.com"},
        )
        await conn.execute(
            text(
                "INSERT INTO conversations (id, channel, owner_id) "
                "VALUES (:id, 'owner_web', :owner)"
            ),
            {"id": owner_conv, "owner": owner_id},
        )
    return Seed(a=a, b=b, owner_id=owner_id, owner_conversation_id=owner_conv)


@pytest.fixture
def bot_sessions(bot_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bot_engine, expire_on_commit=False)


async def _ids(session_or_conn, table: str, column: str = "id") -> set[UUID]:
    return set((await session_or_conn.execute(text(f"SELECT {column} FROM {table}"))).scalars())


def _assert_insufficient_privilege(exc_info: pytest.ExceptionInfo[DBAPIError]) -> None:
    assert isinstance(exc_info.value.orig, psycopg.errors.InsufficientPrivilege), exc_info.value


async def _bot_denied(
    sessions: async_sessionmaker[AsyncSession],
    sql: str,
    params: dict | None = None,
    scope: UUID | None = None,
) -> None:
    """Run one statement as bot (optionally scoped) in its own transaction; expect 42501."""
    async with sessions() as s:
        with pytest.raises(DBAPIError) as exc_info:
            if scope is None:
                async with s.begin():
                    await s.execute(text(sql), params or {})
            else:
                async with customer_scope(s, scope):
                    await s.execute(text(sql), params or {})
    _assert_insufficient_privilege(exc_info)


# ---------------------------------------------------------------------------- 1. no scope


@pytest.mark.parametrize("table", list(CUSTOMER_TABLES))
async def test_bot_without_scope_sees_nothing(seed, bot_sessions, table):
    async with bot_sessions() as s, s.begin():
        count = (await s.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
    assert count == 0


# ---------------------------------------------------------------------------- 2. scoped reads


@pytest.mark.parametrize("table", list(CUSTOMER_TABLES))
async def test_bot_scoped_sees_only_own_rows(seed, bot_sessions, table):
    async with bot_sessions() as s, customer_scope(s, seed.a.account_id):
        visible = await _ids(s, table)
        keys = set(
            (await s.execute(text(f"SELECT {CUSTOMER_TABLES[table]} FROM {table}"))).scalars()
        )
    assert visible == seed.a.rows[table]
    assert keys == {seed.a.account_id}
    assert not visible & seed.b.rows[table]


async def test_bot_scoped_cannot_see_owner_conversations(seed, bot_sessions):
    async with bot_sessions() as s, customer_scope(s, seed.a.account_id):
        visible = await _ids(s, "conversations")
    assert seed.owner_conversation_id not in visible


# ---------------------------------------------------------------------------- 3. scoped writes


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO conversations (channel, customer_account_id) "
        "VALUES ('customer_telegram', :other)",
        "INSERT INTO payment_requests "
        "(customer_account_id, stripe_invoice_id, amount, currency, hosted_url) "
        "VALUES (:other, 'in_cross', 100, 'usd', 'https://x')",
        "INSERT INTO handoffs (customer_account_id, reason) VALUES (:other, 'other')",
    ],
    ids=["conversations", "payment_requests", "handoffs"],
)
async def test_bot_cannot_insert_rows_for_another_customer(seed, bot_sessions, sql):
    await _bot_denied(bot_sessions, sql, {"other": seed.b.account_id}, scope=seed.a.account_id)


async def test_bot_can_insert_rows_for_own_customer(seed, bot_sessions):
    # Positive control for the WITH CHECK tests above: the same statements succeed for A.
    async with bot_sessions() as s, customer_scope(s, seed.a.account_id):
        await s.execute(
            text(
                "INSERT INTO conversations (channel, customer_account_id) "
                "VALUES ('customer_telegram', :me)"
            ),
            {"me": seed.a.account_id},
        )
        await s.execute(
            text(
                "INSERT INTO messages (conversation_id, customer_account_id, role, content) "
                "VALUES (:conv, :me, 'assistant', 'You owe $120.00')"
            ),
            {"conv": seed.a.conversation_id, "me": seed.a.account_id},
        )
        await s.execute(
            text("INSERT INTO handoffs (customer_account_id, reason) VALUES (:me, 'other')"),
            {"me": seed.a.account_id},
        )


async def test_bot_cannot_insert_message_into_other_customers_conversation(seed, bot_sessions):
    # Even claiming B's id (so the trigger's consistency check would pass), RLS rejects it.
    await _bot_denied(
        bot_sessions,
        "INSERT INTO messages (conversation_id, customer_account_id, role, content) "
        "VALUES (:conv, :acct, 'user', 'hi')",
        {"conv": seed.b.conversation_id, "acct": seed.b.account_id},
        scope=seed.a.account_id,
    )
    # And with A's id, B's conversation is invisible, so the trigger refuses it.
    await _bot_denied(
        bot_sessions,
        "INSERT INTO messages (conversation_id, customer_account_id, role, content) "
        "VALUES (:conv, :acct, 'user', 'hi')",
        {"conv": seed.b.conversation_id, "acct": seed.a.account_id},
        scope=seed.a.account_id,
    )


async def test_bot_cannot_insert_message_into_owner_conversation(seed, bot_sessions):
    await _bot_denied(
        bot_sessions,
        "INSERT INTO messages (conversation_id, customer_account_id, role, content) "
        "VALUES (:conv, :acct, 'user', 'hi')",
        {"conv": seed.owner_conversation_id, "acct": seed.a.account_id},
        scope=seed.a.account_id,
    )


# ---------------------------------------------------------------------------- 4. owner tables


@pytest.mark.parametrize("table", OWNER_ONLY_TABLES)
async def test_bot_has_no_access_to_owner_tables(seed, bot_sessions, table):
    await _bot_denied(bot_sessions, f"SELECT 1 FROM {table} LIMIT 1")
    await _bot_denied(bot_sessions, f"SELECT 1 FROM {table} LIMIT 1", scope=seed.a.account_id)


# ---------------------------------------------------------------------------- 5. append-only


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE messages SET content = 'tampered' WHERE customer_account_id = :me",
        "DELETE FROM messages WHERE customer_account_id = :me",
        "SELECT * FROM audit_log",
        "UPDATE audit_log SET action = 'tampered' WHERE customer_account_id = :me",
        "DELETE FROM audit_log WHERE customer_account_id = :me",
    ],
    ids=["update-messages", "delete-messages", "select-audit", "update-audit", "delete-audit"],
)
async def test_bot_cannot_tamper_with_transcript_or_audit(seed, bot_sessions, sql):
    await _bot_denied(bot_sessions, sql, {"me": seed.a.account_id}, scope=seed.a.account_id)


async def test_bot_can_append_audit_for_own_customer_only(seed, bot_sessions, admin_engine):
    action = f"test.audit.{uuid4().hex}"
    async with bot_sessions() as s, customer_scope(s, seed.a.account_id):
        # No RETURNING: bot has no SELECT on audit_log.
        await s.execute(
            text(
                "INSERT INTO audit_log (actor_type, customer_account_id, action) "
                "VALUES ('customer', :me, :action)"
            ),
            {"me": seed.a.account_id, "action": action},
        )
    async with admin_engine.connect() as conn:
        written = (
            await conn.execute(
                text("SELECT count(*) FROM audit_log WHERE action = :action"), {"action": action}
            )
        ).scalar_one()
    assert written == 1

    await _bot_denied(
        bot_sessions,
        "INSERT INTO audit_log (actor_type, customer_account_id, action) "
        "VALUES ('customer', :other, 'test.audit.cross')",
        {"other": seed.b.account_id},
        scope=seed.a.account_id,
    )


async def test_bot_conversation_updates_limited_to_granted_columns(seed, bot_sessions):
    await _bot_denied(
        bot_sessions,
        "UPDATE conversations SET title = 'hijacked' WHERE id = :conv",
        {"conv": seed.a.conversation_id},
        scope=seed.a.account_id,
    )
    async with bot_sessions() as s, customer_scope(s, seed.a.account_id):
        result = await s.execute(
            text(
                "UPDATE conversations SET status = 'closed', last_message_at = now() "
                "WHERE id = :conv"
            ),
            {"conv": seed.a.conversation_id},
        )
        assert result.rowcount == 1
        # B's conversation is invisible, so an update matches zero rows rather than erroring.
        result = await s.execute(
            text("UPDATE conversations SET status = 'closed' WHERE id = :conv"),
            {"conv": seed.b.conversation_id},
        )
        assert result.rowcount == 0


# ---------------------------------------------------------------------------- 6. identity tables


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO telegram_identities (customer_account_id, telegram_user_id, telegram_chat_id) "
        "VALUES (:me, 1, 1)",
        "UPDATE telegram_identities SET revoked_at = now() WHERE customer_account_id = :me",
        "INSERT INTO customer_accounts (stripe_customer_id, display_name) VALUES ('cus_x', 'x')",
        "UPDATE customer_accounts SET display_name = 'renamed' WHERE id = :me",
    ],
    ids=["insert-identity", "update-identity", "insert-account", "update-account"],
)
async def test_bot_cannot_write_identity_tables_directly(seed, bot_sessions, sql):
    await _bot_denied(bot_sessions, sql, {"me": seed.a.account_id}, scope=seed.a.account_id)


# ---------------------------------------------------------------------------- 7. no scope leak


@pytest.fixture
async def single_conn_bot_engine(migrated_db) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(role_url("bot"), pool_size=1, max_overflow=0)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _pid_and_scope(s: AsyncSession) -> tuple[int, str | None]:
    row = (
        await s.execute(
            text("SELECT pg_backend_pid(), current_setting('app.customer_account_id', true)")
        )
    ).one()
    return row[0], row[1]


async def test_scope_does_not_leak_after_commit(seed, single_conn_bot_engine):
    sessions = async_sessionmaker(single_conn_bot_engine, expire_on_commit=False)
    async with sessions() as s, customer_scope(s, seed.a.account_id):
        pid_scoped, scoped_value = await _pid_and_scope(s)
        assert scoped_value == str(seed.a.account_id)
        assert await _ids(s, "conversations") == seed.a.rows["conversations"]

    async with sessions() as s, s.begin():
        pid_after, value_after = await _pid_and_scope(s)
        count = (await s.execute(text("SELECT count(*) FROM conversations"))).scalar_one()
    assert pid_after == pid_scoped, "test must reuse the same pooled connection"
    assert value_after in (None, "")
    assert count == 0


async def test_scope_does_not_leak_after_rollback(seed, single_conn_bot_engine):
    sessions = async_sessionmaker(single_conn_bot_engine, expire_on_commit=False)

    class Boom(Exception):
        pass

    async with sessions() as s:
        with pytest.raises(Boom):
            async with customer_scope(s, seed.a.account_id):
                pid_scoped, _ = await _pid_and_scope(s)
                raise Boom

    async with sessions() as s, s.begin():
        pid_after, value_after = await _pid_and_scope(s)
        count = (await s.execute(text("SELECT count(*) FROM messages"))).scalar_one()
    assert pid_after == pid_scoped
    assert value_after in (None, "")
    assert count == 0


# ---------------------------------------------------------------------------- 8. api role


@pytest.mark.parametrize("table", list(CUSTOMER_TABLES))
async def test_api_sees_all_customers(seed, api_engine, table):
    async with api_engine.connect() as conn:
        visible = await _ids(conn, table)
    assert seed.a.rows[table] <= visible
    assert seed.b.rows[table] <= visible


async def test_api_sees_owner_conversations_and_owner_tables(seed, api_engine):
    async with api_engine.connect() as conn:
        assert seed.owner_conversation_id in await _ids(conn, "conversations")
        assert seed.owner_id in await _ids(conn, "owners")
        for table in OWNER_ONLY_TABLES:
            await conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))


# ---------------------------------------------------------------------------- 9. reporter


@pytest.fixture
async def reporter_engine(migrated_db) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(role_url("reporter"))
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.mark.parametrize("table", [*CUSTOMER_TABLES, "owners", "customer_invites"])
async def test_reporter_has_no_customer_or_credential_data(seed, reporter_engine, table):
    async with reporter_engine.connect() as conn:
        with pytest.raises(DBAPIError) as exc_info:
            await conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
    _assert_insufficient_privilege(exc_info)


# ---------------------------------------------------------------------------- 10. garbage scope


@pytest.mark.parametrize("table", list(CUSTOMER_TABLES))
async def test_garbage_scope_value_errors_instead_of_returning_rows(seed, bot_sessions, table):
    async with bot_sessions() as s:
        with pytest.raises(DBAPIError) as exc_info:
            async with s.begin():
                await s.execute(
                    text("SELECT set_config('app.customer_account_id', 'not-a-uuid', true)")
                )
                await s.execute(text(f"SELECT * FROM {table}"))
    assert isinstance(exc_info.value.orig, psycopg.errors.InvalidTextRepresentation)
