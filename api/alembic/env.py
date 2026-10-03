"""Alembic environment. Migrations always run as the `admin` role (owner of every table).

Uses a sync psycopg engine: migrations are one-shot and don't need async. The URL comes from
`sqlalchemy.url` if a caller set it (tests), else DATABASE_URL.
"""

import os
from logging.config import fileConfig

from sqlalchemy import create_engine, pool

from alembic import context
from payments_assistant.core.models import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or os.environ["DATABASE_URL"]


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
