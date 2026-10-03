"""Shared DB fixtures (a plugin registered in tests/conftest.py, so one test DB per run): a
throwaway `<DB_NAME>_test_<random>` database migrated as admin, plus engines that connect as each
runtime role. RLS tests MUST use `bot_engine`/`api_engine`, never admin (a superuser bypasses RLS).

Runs in the `api-test` compose service, which provides PG_HOST and the DB_* credentials.
"""

import os
import uuid
from collections.abc import AsyncIterator, Iterator

import psycopg
import pytest
from alembic.config import Config
from psycopg import sql
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from alembic import command

PG_HOST = os.environ.get("PG_HOST", "postgres")
DB_NAME = os.environ.get("DB_NAME", "payments_assistant")
# Unique per pytest run so parallel runs (e.g. subagents) never drop each other's database.
TEST_DB = f"{DB_NAME}_test_{uuid.uuid4().hex[:8]}"
ADMIN_USER = os.environ.get("DB_ADMIN_USER", "admin")
PASSWORDS = {
    ADMIN_USER: os.environ.get("DB_ADMIN_PASSWORD", "admin"),
    "api": os.environ.get("DB_API_PASSWORD", "api"),
    "bot": os.environ.get("DB_BOT_PASSWORD", "bot"),
    "reporter": os.environ.get("DB_REPORTER_PASSWORD", "reporter"),
}


def role_url(role: str, db: str = TEST_DB, driver: str = "postgresql+psycopg") -> str:
    return f"{driver}://{role}:{PASSWORDS[role]}@{PG_HOST}:5432/{db}"


def _drop_test_db(admin_dsn: str) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(TEST_DB))
        )


@pytest.fixture(scope="session")
def migrated_db() -> Iterator[str]:
    """Create a fresh test DB, run every migration as admin, and drop it afterwards (also when
    setup itself fails, so broken runs don't leave databases behind)."""
    admin_dsn = role_url(ADMIN_USER, db=DB_NAME, driver="postgresql")
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TEST_DB)))
    try:
        # Roles are cluster-global; CONNECT/USAGE are per database, so grant them here too
        # (the init script only covered the dev database).
        with psycopg.connect(role_url(ADMIN_USER, driver="postgresql"), autocommit=True) as conn:
            conn.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO api, bot, reporter").format(
                    sql.Identifier(TEST_DB)
                )
            )
            conn.execute("GRANT USAGE ON SCHEMA public TO api, bot, reporter")

        cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", role_url(ADMIN_USER))
        cfg.attributes["configure_logger"] = False
        command.upgrade(cfg, "head")
        yield TEST_DB
    finally:
        _drop_test_db(admin_dsn)


async def _engine(role: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(role_url(role))
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
async def admin_engine(migrated_db) -> AsyncIterator[AsyncEngine]:
    async for e in _engine(ADMIN_USER):
        yield e


@pytest.fixture(scope="session")
async def api_engine(migrated_db) -> AsyncIterator[AsyncEngine]:
    async for e in _engine("api"):
        yield e


@pytest.fixture(scope="session")
async def bot_engine(migrated_db) -> AsyncIterator[AsyncEngine]:
    async for e in _engine("bot"):
        yield e
