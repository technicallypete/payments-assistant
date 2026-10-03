"""Runtime roles exist with least privilege; the RLS design depends on these attributes."""

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


async def _assert_least_privilege(engine, role: str) -> None:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "select current_user, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole "
                    "from pg_roles where rolname = current_user"
                )
            )
        ).one()
    assert row.current_user == role
    assert not row.rolsuper
    assert not row.rolbypassrls
    assert not row.rolcreatedb
    assert not row.rolcreaterole


async def test_api_role_cannot_bypass_rls(api_engine):
    await _assert_least_privilege(api_engine, "api")


async def test_bot_role_cannot_bypass_rls(bot_engine):
    await _assert_least_privilege(bot_engine, "bot")


async def test_admin_is_superuser(admin_engine):
    async with admin_engine.connect() as conn:
        is_super = (
            await conn.execute(text("select rolsuper from pg_roles where rolname = current_user"))
        ).scalar_one()
    assert is_super


async def test_migrations_applied(admin_engine):
    async with admin_engine.connect() as conn:
        exists = (
            await conn.execute(text("select to_regclass('public.alembic_version') is not null"))
        ).scalar_one()
    assert exists
