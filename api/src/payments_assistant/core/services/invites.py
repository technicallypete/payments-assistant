"""Telegram deep-link invites (spec §3.2). Redemption happens in the DB via the SECURITY DEFINER
function `redeem_customer_invite`; this module only mints them (owner/seed side)."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from payments_assistant.core.models import CustomerInvite
from payments_assistant.core.security import hash_token, new_token


@dataclass(frozen=True)
class Invite:
    token: str  # raw; shown once, never stored
    url: str
    expires_at: datetime


def invite_url(bot_username: str, token: str) -> str:
    return f"https://t.me/{bot_username}?start={token}"


async def create_invite(
    session: AsyncSession,
    *,
    customer_account_id: UUID,
    bot_username: str,
    ttl_days: int,
    created_by: UUID | None = None,
    now: datetime | None = None,
) -> Invite:
    # Telegram /start payloads allow at most 64 chars of [A-Za-z0-9_-]; 24 bytes → 32 chars.
    token = new_token(24)
    expires_at = (now or datetime.now(UTC)) + timedelta(days=ttl_days)
    session.add(
        CustomerInvite(
            customer_account_id=customer_account_id,
            token_hash=hash_token(token),
            expires_at=expires_at,
            created_by=created_by,
        )
    )
    await session.flush()
    return Invite(token=token, url=invite_url(bot_username, token), expires_at=expires_at)
