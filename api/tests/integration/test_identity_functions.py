"""SECURITY DEFINER identity functions: the only way the bot learns who a Telegram user is."""

import hashlib
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _tg_id() -> int:
    return uuid4().int % 9_000_000_000 + 1_000_000_000


async def _account(admin_engine, status: str = "active") -> UUID:
    account_id = uuid4()
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "insert into customer_accounts (id, stripe_customer_id, display_name, status) "
                "values (:id, :cus, 'Test Customer', :status)"
            ),
            {"id": account_id, "cus": f"cus_{account_id.hex[:12]}", "status": status},
        )
    return account_id


async def _invite(admin_engine, account_id: UUID, token: str, ttl: str = "1 day") -> None:
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "insert into customer_invites (customer_account_id, token_hash, expires_at) "
                "values (:a, :h, now() + cast(:ttl as interval))"
            ),
            {"a": account_id, "h": _hash(token), "ttl": ttl},
        )


async def _redeem(bot_engine, token: str, tg_user: int):
    async with bot_engine.begin() as conn:
        return (
            await conn.execute(
                text("select redeem_customer_invite(:h, :u, :c, 'someone')"),
                {"h": _hash(token), "u": tg_user, "c": tg_user},
            )
        ).scalar_one()


async def _resolve(bot_engine, tg_user: int):
    async with bot_engine.begin() as conn:
        return (
            await conn.execute(text("select resolve_telegram_identity(:u)"), {"u": tg_user})
        ).scalar_one()


async def test_redeem_links_and_resolve_finds_account(admin_engine, bot_engine):
    account = await _account(admin_engine)
    token, tg = uuid4().hex, _tg_id()
    await _invite(admin_engine, account, token)

    assert await _redeem(bot_engine, token, tg) == account
    assert await _resolve(bot_engine, tg) == account


async def test_invite_is_single_use(admin_engine, bot_engine):
    account = await _account(admin_engine)
    token = uuid4().hex
    await _invite(admin_engine, account, token)

    assert await _redeem(bot_engine, token, _tg_id()) == account
    assert await _redeem(bot_engine, token, _tg_id()) is None


async def test_expired_invite_rejected(admin_engine, bot_engine):
    account = await _account(admin_engine)
    token = uuid4().hex
    await _invite(admin_engine, account, token, ttl="-1 minute")
    assert await _redeem(bot_engine, token, _tg_id()) is None


async def test_unknown_token_rejected(bot_engine):
    assert await _redeem(bot_engine, uuid4().hex, _tg_id()) is None


async def test_disabled_account_cannot_link_or_resolve(admin_engine, bot_engine):
    account = await _account(admin_engine)
    token, tg = uuid4().hex, _tg_id()
    await _invite(admin_engine, account, token)
    assert await _redeem(bot_engine, token, tg) == account

    async with admin_engine.begin() as conn:
        await conn.execute(
            text("update customer_accounts set status = 'disabled' where id = :a"), {"a": account}
        )
    assert await _resolve(bot_engine, tg) is None

    disabled = await _account(admin_engine, status="disabled")
    token2 = uuid4().hex
    await _invite(admin_engine, disabled, token2)
    assert await _redeem(bot_engine, token2, _tg_id()) is None


async def test_relinking_revokes_previous_link(admin_engine, bot_engine):
    first, second = await _account(admin_engine), await _account(admin_engine)
    t1, t2, tg = uuid4().hex, uuid4().hex, _tg_id()
    await _invite(admin_engine, first, t1)
    await _invite(admin_engine, second, t2)

    assert await _redeem(bot_engine, t1, tg) == first
    assert await _redeem(bot_engine, t2, tg) == second
    assert await _resolve(bot_engine, tg) == second

    async with admin_engine.connect() as conn:
        active = (
            await conn.execute(
                text(
                    "select count(*) from telegram_identities "
                    "where telegram_user_id = :u and revoked_at is null"
                ),
                {"u": tg},
            )
        ).scalar_one()
    assert active == 1


async def test_revoke_unlinks(admin_engine, bot_engine):
    account = await _account(admin_engine)
    token, tg = uuid4().hex, _tg_id()
    await _invite(admin_engine, account, token)
    await _redeem(bot_engine, token, tg)

    async with bot_engine.begin() as conn:
        revoked = (
            await conn.execute(text("select revoke_telegram_identity(:u)"), {"u": tg})
        ).scalar_one()
    assert revoked is True
    assert await _resolve(bot_engine, tg) is None

    async with bot_engine.begin() as conn:
        again = (
            await conn.execute(text("select revoke_telegram_identity(:u)"), {"u": tg})
        ).scalar_one()
    assert again is False


@pytest.mark.parametrize(
    "call",
    [
        "select redeem_customer_invite('x', 1, 1, null)",
        "select resolve_telegram_identity(1)",
        "select revoke_telegram_identity(1)",
    ],
)
async def test_api_role_cannot_execute_bot_identity_functions(api_engine, call):
    with pytest.raises(DBAPIError, match="permission denied"):
        async with api_engine.begin() as conn:
            await conn.execute(text(call))
