"""Confirm / cancel owner action proposals. This is the only path that moves money."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from payments_assistant.core.models import OwnerAction
from payments_assistant.core.services import actions
from payments_assistant.http.deps import Owner, Session, State

router = APIRouter(prefix="/actions", tags=["actions"])


class ActionOut(BaseModel):
    id: UUID
    action_type: str
    preview: str
    status: str
    stripe_object_id: str | None
    error: str | None
    expires_at: datetime
    created_at: datetime


def _out(a: OwnerAction) -> ActionOut:
    return ActionOut(
        id=a.id,
        action_type=a.action_type,
        preview=a.preview,
        status=a.status,
        stripe_object_id=a.stripe_object_id,
        error=a.error,
        expires_at=a.expires_at,
        created_at=a.created_at,
    )


@router.get("", response_model=list[ActionOut])
async def list_actions(
    owner: Owner,
    session: Session,
    state: State,
    status_filter: Literal["proposed", "all"] = "proposed",
) -> list[ActionOut]:
    """Pending web proposals (default): still `proposed` and not yet past their expiry."""
    q = select(OwnerAction).where(
        OwnerAction.owner_id == owner.owner_id, OwnerAction.api_key_id.is_(None)
    )
    if status_filter == "proposed":
        q = q.where(OwnerAction.status == "proposed", OwnerAction.expires_at > state.clock())
    rows = (await session.execute(q.order_by(OwnerAction.created_at.desc()).limit(50))).scalars()
    return [_out(a) for a in rows]


@router.post("/{action_id}/confirm", response_model=ActionOut)
async def confirm_action(
    action_id: UUID, owner: Owner, session: Session, state: State
) -> ActionOut:
    """Executes the proposal. Returns 200 with status executed / failed / expired."""
    try:
        action = await actions.confirm(
            session,
            state.owner_gateway(),
            action_id=action_id,
            owner_id=owner.owner_id,
            now=state.clock(),
        )
    except actions.ActionNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except actions.ActionStateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _out(action)


@router.post("/{action_id}/cancel", response_model=ActionOut)
async def cancel_action(action_id: UUID, owner: Owner, session: Session) -> ActionOut:
    try:
        action = await actions.cancel(session, action_id=action_id, owner_id=owner.owner_id)
    except actions.ActionNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except actions.ActionStateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _out(action)
