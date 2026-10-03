"""Owner API keys for the MCP surface. Managed with the owner's web session; the raw key is
returned exactly once, at creation."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from payments_assistant.core.client_config import client_snippets
from payments_assistant.core.services import api_keys
from payments_assistant.http.deps import Owner, Session, State

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


class ApiKeyOut(BaseModel):
    id: UUID
    name: str
    display_prefix: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class CreateKeyIn(BaseModel):
    name: str = Field("API key", min_length=1, max_length=80)


class ClientConfigOut(BaseModel):
    claude_code: str
    claude_desktop: str


class CreatedKeyOut(BaseModel):
    id: UUID
    name: str
    display_prefix: str
    key: str  # shown once
    mcp_url: str
    config: ClientConfigOut


@router.get("", response_model=list[ApiKeyOut])
async def list_keys(owner: Owner, session: Session) -> list[ApiKeyOut]:
    return [
        ApiKeyOut(
            id=k.id,
            name=k.name,
            display_prefix=k.key_prefix,
            created_at=k.created_at,
            last_used_at=k.last_used_at,
            revoked_at=k.revoked_at,
        )
        for k in await api_keys.list_keys(session, owner_id=owner.owner_id)
    ]


@router.post("", response_model=CreatedKeyOut, status_code=status.HTTP_201_CREATED)
async def create_key(
    body: CreateKeyIn, owner: Owner, session: Session, state: State
) -> CreatedKeyOut:
    new = await api_keys.create(session, owner_id=owner.owner_id, name=body.name, now=state.clock())
    snippets = client_snippets(new.key)
    return CreatedKeyOut(
        id=new.id,
        name=new.name,
        display_prefix=new.display_prefix,
        key=new.key,
        mcp_url=snippets.mcp_url,
        config=ClientConfigOut(
            claude_code=snippets.claude_code, claude_desktop=snippets.claude_desktop
        ),
    )


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(key_id: UUID, owner: Owner, session: Session, state: State) -> None:
    if not await api_keys.revoke(
        session, owner_id=owner.owner_id, key_id=key_id, now=state.clock()
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such active key.")
