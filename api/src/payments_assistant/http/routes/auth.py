"""Owner login/logout. Called by the Next.js route handlers, which keep the token in an httpOnly
cookie; the browser never sees it."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from payments_assistant.core.services import auth
from payments_assistant.http.deps import Owner, Session, State, _bearer

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=500)


class OwnerOut(BaseModel):
    id: UUID
    email: str


class LoginOut(BaseModel):
    token: str
    expires_at: datetime
    owner: OwnerOut


def _client_ip(request: Request) -> str | None:
    # The Next proxy is the only caller; it forwards the browser's address.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.client.host if request.client else None


@router.post("/login", response_model=LoginOut)
async def login(body: LoginIn, request: Request, session: Session, state: State) -> LoginOut:
    try:
        result = await auth.login(
            session,
            email=body.email,
            password=body.password,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            settings=state.settings,
            now=state.clock(),
        )
    except auth.TooManyAttempts as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Try again later."
        ) from exc
    except auth.InvalidCredentials:
        # Commit the failed attempt (it counts toward the throttle) before rejecting.
        await session.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password.") from None
    return LoginOut(
        token=result.token,
        expires_at=result.expires_at,
        owner=OwnerOut(id=result.owner_id, email=result.email),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    owner: Owner,
    session: Session,
    state: State,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    token = _bearer(authorization)
    if token:
        await auth.logout(session, token, now=state.clock())


@router.get("/me", response_model=OwnerOut)
async def me(owner: Owner) -> OwnerOut:
    return OwnerOut(id=owner.owner_id, email=owner.email)
