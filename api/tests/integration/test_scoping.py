"""`customer_scope` sets the RLS key transaction-locally and nothing leaks past it."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from payments_assistant.core.scoping import customer_scope

pytestmark = pytest.mark.integration

_CURRENT = text("select current_setting('app.customer_account_id', true)")


async def test_scope_sets_setting_inside_transaction(bot_engine):
    maker = async_sessionmaker(bot_engine)
    account = uuid4()
    async with maker() as session, customer_scope(session, account):
        assert (await session.execute(_CURRENT)).scalar_one() == str(account)
        assert (await session.execute(text("select app_current_customer()"))).scalar_one() == (
            account
        )


async def test_scope_cleared_after_commit_and_rollback(bot_engine):
    maker = async_sessionmaker(bot_engine)
    async with maker() as session:
        async with customer_scope(session, uuid4()):
            pass
        assert (await session.execute(_CURRENT)).scalar_one() in (None, "")
        await session.rollback()

        with pytest.raises(RuntimeError):
            async with customer_scope(session, uuid4()):
                raise RuntimeError("boom")
        assert (await session.execute(_CURRENT)).scalar_one() in (None, "")


async def test_scope_rejects_non_uuid(bot_engine):
    maker = async_sessionmaker(bot_engine)
    async with maker() as session:
        with pytest.raises(TypeError):
            async with customer_scope(session, "not-a-uuid"):  # type: ignore[arg-type]
                pass
