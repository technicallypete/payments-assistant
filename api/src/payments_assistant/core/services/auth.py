"""Owner authentication: password login → opaque session token (spec §3.1).

The raw token goes to the Next proxy (which keeps it in an httpOnly cookie) and is never stored;
we keep its SHA-256. Sessions slide: each use extends `expires_at` by SESSION_TTL_DAYS.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from payments_assistant.core.config import Settings
from payments_assistant.core.models import LoginAttempt, Owner, OwnerSession
from payments_assistant.core.security import hash_password, hash_token, new_token, verify_password
from payments_assistant.core.services import audit

__all__ = [
    "InvalidCredentials",
    "LoginResult",
    "TooManyAttempts",
    "hash_token",
    "login",
    "logout",
    "resolve_session",
]

# Verified against when the email is unknown, so response time doesn't reveal which emails exist.
_DUMMY_HASH = hash_password("not-a-real-password")
_LAST_SEEN_GRANULARITY = timedelta(minutes=1)


class InvalidCredentials(Exception):
    pass


class TooManyAttempts(Exception):
    pass


@dataclass(frozen=True)
class LoginResult:
    token: str
    owner_id: UUID
    email: str
    expires_at: datetime


async def login(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    ip: str | None,
    user_agent: str | None,
    settings: Settings,
    now: datetime,
) -> LoginResult:
    email_norm = email.strip().lower()
    window_start = now - timedelta(minutes=settings.login_window_minutes)
    failures = (
        await session.execute(
            select(func.count())
            .select_from(LoginAttempt)
            .where(
                func.lower(LoginAttempt.email) == email_norm,
                LoginAttempt.succeeded.is_(False),
                LoginAttempt.created_at >= window_start,
            )
        )
    ).scalar_one()
    if failures >= settings.login_max_attempts:
        raise TooManyAttempts

    owner = (
        await session.execute(select(Owner).where(func.lower(Owner.email) == email_norm))
    ).scalar_one_or_none()
    ok = verify_password(owner.password_hash if owner else _DUMMY_HASH, password) and owner
    session.add(LoginAttempt(email=email_norm, ip=ip, succeeded=bool(ok), created_at=now))
    if not ok:
        await session.flush()
        raise InvalidCredentials

    token = new_token(32)
    expires_at = now + timedelta(days=settings.session_ttl_days)
    session.add(
        OwnerSession(
            owner_id=owner.id,
            token_hash=hash_token(token),
            created_at=now,
            last_seen_at=now,
            expires_at=expires_at,
            user_agent=(user_agent or "")[:300] or None,
            ip=ip,
        )
    )
    owner.last_login_at = now
    await session.flush()
    await audit.record(session, actor_type="owner", actor_id=str(owner.id), action="login")
    return LoginResult(token=token, owner_id=owner.id, email=owner.email, expires_at=expires_at)


async def resolve_session(
    session: AsyncSession, token: str, *, settings: Settings, now: datetime
) -> Owner | None:
    row = (
        await session.execute(
            select(OwnerSession, Owner)
            .join(Owner, Owner.id == OwnerSession.owner_id)
            .where(OwnerSession.token_hash == hash_token(token))
        )
    ).one_or_none()
    if row is None:
        return None
    sess, owner = row
    if sess.revoked_at is not None or sess.expires_at <= now:
        return None
    if now - sess.last_seen_at >= _LAST_SEEN_GRANULARITY:
        sess.last_seen_at = now
        sess.expires_at = now + timedelta(days=settings.session_ttl_days)
    return owner


async def logout(session: AsyncSession, token: str, *, now: datetime) -> bool:
    result = await session.execute(
        update(OwnerSession)
        .where(OwnerSession.token_hash == hash_token(token), OwnerSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    return result.rowcount > 0
