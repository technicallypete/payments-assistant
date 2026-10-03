"""One definition per tool (spec §2.5). Adapters (LangChain loop, MCP) read `REGISTRY`.

A tool is an async function `(ctx: ToolContext, args: InputModel) -> OutputModel`. The context
carries the authenticated actor, built by the adapter from the session or Telegram identity,
never from arguments. Customer tools therefore cannot even express "another customer".
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from payments_assistant.core.config import Settings
from payments_assistant.core.stripe_gateway import CustomerStripeGateway, OwnerStripeGateway

Audience = Literal["owner", "customer"]


@dataclass
class ToolContext:
    session: AsyncSession
    settings: Settings
    now: datetime
    owner_id: UUID | None = None
    api_key_id: UUID | None = None  # set when called over MCP
    owner_gateway: OwnerStripeGateway | None = None
    customer_account_id: UUID | None = None
    customer_gateway: CustomerStripeGateway | None = None
    conversation_id: UUID | None = None
    # Proposals created during this turn, so adapters can surface Confirm cards.
    proposals: list[Any] = field(default_factory=list)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.settings.business_timezone)

    @property
    def currency(self) -> str:
        return self.settings.default_currency

    def owner(self) -> tuple[UUID, OwnerStripeGateway]:
        if self.owner_id is None or self.owner_gateway is None:
            raise PermissionError("owner tool called without an owner context")
        return self.owner_id, self.owner_gateway

    def customer(self) -> tuple[UUID, CustomerStripeGateway]:
        if self.customer_account_id is None or self.customer_gateway is None:
            raise PermissionError("customer tool called without a customer context")
        return self.customer_account_id, self.customer_gateway


class ToolError(Exception):
    """An expected, user-facing failure (bad reference, nothing found). Shown to the model as-is."""


ToolFn = Callable[[ToolContext, Any], Awaitable[BaseModel]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    audience: Audience
    mutating: bool
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    fn: ToolFn
    label: str  # short status text for the UI, e.g. "Looking up payments"

    def json_schema(self) -> dict[str, Any]:
        """OpenAI-style function schema (what LangChain's bind_tools and MCP both accept)."""
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": schema},
        }

    async def run(self, ctx: ToolContext, raw_args: dict[str, Any]) -> BaseModel:
        args = self.input_model.model_validate(raw_args)
        return await self.fn(ctx, args)


REGISTRY: dict[str, ToolSpec] = {}


def tool(
    *,
    name: str,
    audience: Audience,
    input_model: type[BaseModel],
    output_model: type[BaseModel],
    label: str,
    mutating: bool = False,
) -> Callable[[ToolFn], ToolFn]:
    def register(fn: ToolFn) -> ToolFn:
        if name in REGISTRY:
            raise ValueError(f"duplicate tool {name}")
        description = (fn.__doc__ or "").strip()
        if not description:
            raise ValueError(
                f"tool {name} needs a docstring (it becomes the model-facing description)"
            )
        REGISTRY[name] = ToolSpec(
            name=name,
            description=description,
            audience=audience,
            mutating=mutating,
            input_model=input_model,
            output_model=output_model,
            fn=fn,
            label=label,
        )
        return fn

    return register


def tools_for(audience: Audience) -> list[ToolSpec]:
    return [t for t in REGISTRY.values() if t.audience == audience]
