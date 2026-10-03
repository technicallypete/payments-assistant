"""Everything the HTTP layer needs, built once at startup and swappable in tests.

Routes never construct Stripe clients, models, or engines themselves; they ask `AppState`. Tests
build an `AppState` with fakes (fake gateway, scripted model, frozen clock) and pass it to
`create_app(state=...)`.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from langchain_core.language_models import BaseChatModel
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from payments_assistant.core.config import Settings
from payments_assistant.core.stripe_gateway import OwnerStripeGateway


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class AppState:
    settings: Settings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    owner_gateway: Callable[[], OwnerStripeGateway]
    model: Callable[[str], BaseChatModel]  # purpose ("agent" | "summary") -> chat model
    clock: Callable[[], datetime] = field(default=_utcnow)

    def model_name(self, purpose: str = "agent") -> str:
        s = self.settings
        return s.summary_model if purpose == "summary" else s.llm_model


def build_default_state(settings: Settings) -> AppState:
    """Production wiring: real DB engine, live Stripe, real LLM."""
    from payments_assistant.core.db import make_engine, make_sessionmaker
    from payments_assistant.core.llm import get_chat_model
    from payments_assistant.core.stripe_live import LiveOwnerGateway, make_client

    engine = make_engine(settings.database_url)
    client = make_client(settings)
    return AppState(
        settings=settings,
        engine=engine,
        sessionmaker=make_sessionmaker(engine),
        owner_gateway=lambda: LiveOwnerGateway(client),
        model=lambda purpose: get_chat_model(settings, purpose=purpose),
    )
