"""Request dependencies: app state, DB session, and owner authentication."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from payments_assistant.core.services import auth
from payments_assistant.http.state import AppState


def get_state(request: Request) -> AppState:
    return request.app.state.app_state


State = Annotated[AppState, Depends(get_state)]


async def db_session(state: State) -> AsyncIterator[AsyncSession]:
    """One transaction per request, committed when the handler returns normally.

    Streaming endpoints must NOT use this for work done while streaming: open their own session
    inside the generator (the dependency is torn down before the body streams)."""
    async with state.sessionmaker() as session, session.begin():
        yield session


Session = Annotated[AsyncSession, Depends(db_session)]


@dataclass(frozen=True)
class OwnerPrincipal:
    owner_id: UUID
    email: str
    session_token_hash: str


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


async def require_owner(
    state: State,
    authorization: Annotated[str | None, Header()] = None,
) -> OwnerPrincipal:
    """Owner session tokens only (`pa_session` cookie → Bearer, via the Next proxy). API keys
    (`pak_...`) are rejected here: they're only valid on /mcp."""
    token = _bearer(authorization)
    if token is None or token.startswith("pak_"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in.")
    async with state.sessionmaker() as session, session.begin():
        owner = await auth.resolve_session(
            session, token, settings=state.settings, now=state.clock()
        )
    if owner is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in.")
    return OwnerPrincipal(
        owner_id=owner.id, email=owner.email, session_token_hash=auth.hash_token(token)
    )


Owner = Annotated[OwnerPrincipal, Depends(require_owner)]
