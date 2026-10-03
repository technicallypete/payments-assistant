"""SQLAlchemy models. Tables arrive in Phase 2; `Base.metadata` is Alembic's autogenerate target."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
