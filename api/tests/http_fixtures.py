"""HTTP test harness (plugin registered in tests/conftest.py).

`make_app(...)` builds the real FastAPI app around an `AppState` with fakes: the `api`-role engine
from the throwaway test DB, a FakeOwnerGateway (demo account), a ScriptedChatModel, and a frozen
clock. `owner_client` is an httpx client already signed in as a fresh owner.
"""

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

import httpx
import pytest
from langchain_core.messages import AIMessage
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from payments_assistant.core.config import Settings
from payments_assistant.core.security import hash_password
from payments_assistant.core.services import auth
from payments_assistant.http.app import create_app
from payments_assistant.http.state import AppState
from tests.demo_data import NOW, owner_gateway
from tests.fake_llm import ScriptedChatModel
from tests.fakes import FakeOwnerGateway


@dataclass
class Harness:
    state: AppState
    gateway: FakeOwnerGateway
    models: list[ScriptedChatModel]  # every model instance handed out, in order
    script: list[AIMessage]

    def set_script(self, *turns: AIMessage) -> None:
        self.script[:] = list(turns)


def make_harness(
    api_engine: AsyncEngine,
    settings: Settings,
    *,
    now: datetime = NOW,
    gateway: FakeOwnerGateway | None = None,
) -> Harness:
    gw = gateway or owner_gateway()
    script: list[AIMessage] = []
    models: list[ScriptedChatModel] = []

    def model(purpose: str) -> ScriptedChatModel:
        m = ScriptedChatModel(script=list(script))
        models.append(m)
        return m

    state = AppState(
        settings=settings,
        engine=api_engine,
        sessionmaker=async_sessionmaker(api_engine, expire_on_commit=False),
        owner_gateway=lambda: gw,
        model=model,
        clock=lambda: now,
    )
    return Harness(state=state, gateway=gw, models=models, script=script)


@pytest.fixture
def harness(api_engine, settings) -> Harness:
    return make_harness(api_engine, settings)


@pytest.fixture
async def client(harness) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(state=harness.state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as c:
        yield c


@dataclass
class OwnerCreds:
    owner_id: UUID
    email: str
    password: str
    token: str


async def create_owner(
    admin_engine: AsyncEngine, password: str = "s3cret-pass"
) -> tuple[UUID, str]:
    from sqlalchemy import text

    owner_id = uuid4()
    email = f"owner-{owner_id.hex[:8]}@example.com"
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("insert into owners (id, email, password_hash) values (:id, :e, :h)"),
            {"id": owner_id, "e": email, "h": hash_password(password)},
        )
    return owner_id, email


@pytest.fixture
async def owner(admin_engine, harness) -> OwnerCreds:
    password = "s3cret-pass"
    owner_id, email = await create_owner(admin_engine, password)
    async with harness.state.sessionmaker() as s, s.begin():
        result = await auth.login(
            s,
            email=email,
            password=password,
            ip=None,
            user_agent="pytest",
            settings=harness.state.settings,
            now=harness.state.clock(),
        )
    return OwnerCreds(owner_id=owner_id, email=email, password=password, token=result.token)


@pytest.fixture
async def owner_client(client, owner) -> httpx.AsyncClient:
    client.headers["Authorization"] = f"Bearer {owner.token}"
    return client


def parse_sse(body: str) -> list[dict]:
    """Parse an SSE body into a list of event payloads (the `data:` JSON)."""
    events = []
    for block in body.strip().split("\n\n"):
        data = [line[len("data: ") :] for line in block.splitlines() if line.startswith("data: ")]
        if data:
            events.append(json.loads("\n".join(data)))
    return events


SseParser = Callable[[str], list[dict]]
