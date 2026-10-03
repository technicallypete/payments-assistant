"""Owner API keys for the MCP surface (spec §3.3).

Format `pak_<token>`; only the SHA-256 is stored, plus a short display prefix. Keys are accepted
ONLY on /mcp (owner session tokens are rejected there, and keys are rejected everywhere else).
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from payments_assistant.core.models import Owner, OwnerApiKey
from payments_assistant.core.security import hash_token, new_token
from payments_assistant.core.services import audit

KEY_PREFIX = "pak_"
_LAST_USED_GRANULARITY_SECONDS = 60


@dataclass(frozen=True)
class NewKey:
    id: UUID
    name: str
    key: str  # shown once; never stored
    display_prefix: str


@dataclass(frozen=True)
class KeyPrincipal:
    owner_id: UUID
    owner_email: str
    key_id: UUID


def is_api_key(token: str) -> bool:
    return token.startswith(KEY_PREFIX)


async def create(session: AsyncSession, *, owner_id: UUID, name: str, now: datetime) -> NewKey:
    raw = KEY_PREFIX + new_token(32)
    row = OwnerApiKey(
        owner_id=owner_id,
        name=name.strip()[:80] or "API key",
        key_prefix=raw[:12],
        key_hash=hash_token(raw),
        created_at=now,
    )
    session.add(row)
    await session.flush()
    await audit.record(
        session,
        actor_type="owner",
        actor_id=str(owner_id),
        action="api_key_created",
        target=str(row.id),
    )
    return NewKey(id=row.id, name=row.name, key=raw, display_prefix=row.key_prefix)


async def resolve(session: AsyncSession, raw: str, *, now: datetime) -> KeyPrincipal | None:
    if not is_api_key(raw):
        return None
    row = (
        await session.execute(
            select(OwnerApiKey, Owner)
            .join(Owner, Owner.id == OwnerApiKey.owner_id)
            .where(OwnerApiKey.key_hash == hash_token(raw), OwnerApiKey.revoked_at.is_(None))
        )
    ).one_or_none()
    if row is None:
        return None
    key, owner = row
    if key.last_used_at is None or (now - key.last_used_at).total_seconds() >= (
        _LAST_USED_GRANULARITY_SECONDS
    ):
        key.last_used_at = now
    return KeyPrincipal(owner_id=owner.id, owner_email=owner.email, key_id=key.id)


async def list_keys(session: AsyncSession, *, owner_id: UUID) -> list[OwnerApiKey]:
    return list(
        (
            await session.execute(
                select(OwnerApiKey)
                .where(OwnerApiKey.owner_id == owner_id)
                .order_by(OwnerApiKey.created_at.desc())
            )
        ).scalars()
    )


async def revoke(session: AsyncSession, *, owner_id: UUID, key_id: UUID, now: datetime) -> bool:
    key = await session.get(OwnerApiKey, key_id)
    if key is None or key.owner_id != owner_id or key.revoked_at is not None:
        return False
    key.revoked_at = now
    await audit.record(
        session,
        actor_type="owner",
        actor_id=str(owner_id),
        action="api_key_revoked",
        target=str(key_id),
    )
    return True
