"""SQLAlchemy models mirroring the hand-written migrations (alembic/versions/0001, 0002).

The migrations are the source of truth for DDL, RLS, and grants; these models are for querying.
`tests/integration/test_schema.py` checks that every mapped column exists in the migrated DB.

Primary keys get client-side uuid4 defaults so INSERTs never depend on RETURNING. That matters for
the `bot` role, which may INSERT into `audit_log` but has no SELECT policy to read the row back.
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
    }


def _pk() -> Mapped[UUID]:
    return mapped_column(primary_key=True, default=uuid4)


def _now() -> Mapped[datetime]:
    return mapped_column(server_default=func.now())


# ---------------------------------------------------------------------------------- owner side


class Owner(Base):
    __tablename__ = "owners"
    id: Mapped[UUID] = _pk()
    email: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    last_login_at: Mapped[datetime | None]


class OwnerSession(Base):
    __tablename__ = "owner_sessions"
    id: Mapped[UUID] = _pk()
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("owners.id"))
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = _now()
    last_seen_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    user_agent: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(INET)


class OwnerApiKey(Base):
    __tablename__ = "owner_api_keys"
    id: Mapped[UUID] = _pk()
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("owners.id"))
    name: Mapped[str] = mapped_column(Text)
    key_prefix: Mapped[str] = mapped_column(Text)
    key_hash: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = _now()
    last_used_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id: Mapped[UUID] = _pk()
    email: Mapped[str] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(INET)
    succeeded: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = _now()


# ---------------------------------------------------------------------------------- customers


class CustomerAccount(Base):
    __tablename__ = "customer_accounts"
    id: Mapped[UUID] = _pk()
    stripe_customer_id: Mapped[str] = mapped_column(Text, unique=True)
    display_name: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="active")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class CustomerInvite(Base):
    __tablename__ = "customer_invites"
    id: Mapped[UUID] = _pk()
    customer_account_id: Mapped[UUID] = mapped_column(ForeignKey("customer_accounts.id"))
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]
    used_by_telegram_user_id: Mapped[int | None] = mapped_column(BigInteger)
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("owners.id"))
    created_at: Mapped[datetime] = _now()


class TelegramIdentity(Base):
    __tablename__ = "telegram_identities"
    id: Mapped[UUID] = _pk()
    customer_account_id: Mapped[UUID] = mapped_column(ForeignKey("customer_accounts.id"))
    telegram_user_id: Mapped[int] = mapped_column(BigInteger)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger)
    telegram_username: Mapped[str | None] = mapped_column(Text)
    invite_id: Mapped[UUID | None] = mapped_column(ForeignKey("customer_invites.id"))
    linked_at: Mapped[datetime] = _now()
    revoked_at: Mapped[datetime | None]


# ---------------------------------------------------------------------------------- conversations


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[UUID] = _pk()
    channel: Mapped[str] = mapped_column(Text)  # owner_web | customer_telegram
    customer_account_id: Mapped[UUID | None] = mapped_column(ForeignKey("customer_accounts.id"))
    owner_id: Mapped[UUID | None] = mapped_column(ForeignKey("owners.id"))
    external_chat_id: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="open")
    created_at: Mapped[datetime] = _now()
    last_message_at: Mapped[datetime] = _now()


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[UUID] = _pk()
    # Insertion order (identity column, migration 0003); timestamps can tie within a turn.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True))
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"))
    customer_account_id: Mapped[UUID | None]
    role: Mapped[str] = mapped_column(Text)  # user | assistant | tool
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(Text, default="complete")
    tool_name: Mapped[str | None] = mapped_column(Text)
    tool_payload: Mapped[dict[str, Any] | None]
    llm_model: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _now()


# ---------------------------------------------------------------------------------- payments


class PaymentRequest(Base):
    __tablename__ = "payment_requests"
    id: Mapped[UUID] = _pk()
    customer_account_id: Mapped[UUID] = mapped_column(ForeignKey("customer_accounts.id"))
    conversation_id: Mapped[UUID | None] = mapped_column(ForeignKey("conversations.id"))
    stripe_invoice_id: Mapped[str] = mapped_column(Text)
    amount: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    hosted_url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="link_sent")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class Handoff(Base):
    __tablename__ = "handoffs"
    id: Mapped[UUID] = _pk()
    customer_account_id: Mapped[UUID] = mapped_column(ForeignKey("customer_accounts.id"))
    conversation_id: Mapped[UUID | None] = mapped_column(ForeignKey("conversations.id"))
    reason: Mapped[str] = mapped_column(Text)
    stripe_invoice_id: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str | None] = mapped_column(String(3))
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(Text, default="open")
    resolved_by: Mapped[UUID | None] = mapped_column(ForeignKey("owners.id"))
    resolved_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = _now()


# --------------------------------------------------------------------------- owner actions etc.


class OwnerAction(Base):
    __tablename__ = "owner_actions"
    id: Mapped[UUID] = _pk()
    conversation_id: Mapped[UUID | None] = mapped_column(ForeignKey("conversations.id"))
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("owners.id"))
    api_key_id: Mapped[UUID | None] = mapped_column(ForeignKey("owner_api_keys.id"))
    action_type: Mapped[str] = mapped_column(Text)
    params: Mapped[dict[str, Any]]
    preview: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="proposed")
    idempotency_key: Mapped[UUID] = mapped_column(default=uuid4, unique=True)
    stripe_object_id: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime]
    confirmed_at: Mapped[datetime | None]
    executed_at: Mapped[datetime | None]


class DailySummary(Base):
    __tablename__ = "daily_summaries"
    id: Mapped[UUID] = _pk()
    summary_date: Mapped[date] = mapped_column(Date)
    timezone: Mapped[str] = mapped_column(Text)
    stats: Mapped[dict[str, Any]]
    summary: Mapped[str] = mapped_column(Text)
    llm_model: Mapped[str] = mapped_column(Text)
    generated_at: Mapped[datetime] = _now()


class StripeEvent(Base):
    __tablename__ = "stripe_events"
    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    type: Mapped[str] = mapped_column(Text)
    livemode: Mapped[bool] = mapped_column(Boolean)
    payload: Mapped[dict[str, Any]]
    received_at: Mapped[datetime] = _now()
    processed_at: Mapped[datetime | None]
    error: Mapped[str | None] = mapped_column(Text)


class AuditLog(Base):
    __tablename__ = "audit_log"
    # Never fetch server defaults back: the bot can INSERT here but not SELECT.
    __mapper_args__ = {"eager_defaults": False}
    id: Mapped[UUID] = _pk()
    actor_type: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[str | None] = mapped_column(Text)
    customer_account_id: Mapped[UUID | None]
    action: Mapped[str] = mapped_column(Text)
    target: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any] | None]
    created_at: Mapped[datetime] = _now()
