"""Owner mutations: propose → confirm → execute (spec §5.2, §2.5).

The LLM can only call `propose`. Execution happens in `confirm`, which a human triggers: a button
in the web UI, or an MCP `confirm_action` call made with the same API key that proposed it.
Every Stripe call carries the action's idempotency key, so a retried execution can't double-charge
or double-refund.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from payments_assistant.core.models import OwnerAction
from payments_assistant.core.services import audit
from payments_assistant.core.stripe_gateway import OwnerStripeGateway

ActionType = Literal["refund", "create_invoice", "send_invoice", "create_payment_link"]


# ---- validated params per action type (stored as JSON in owner_actions.params)


class RefundParams(BaseModel):
    charge_id: str
    amount: int | None = Field(None, gt=0)  # None = full refund


class CreateInvoiceParams(BaseModel):
    customer_id: str
    amount: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    description: str = Field(min_length=1, max_length=500)
    due_date: date


class SendInvoiceParams(BaseModel):
    invoice_id: str


class CreatePaymentLinkParams(BaseModel):
    amount: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    description: str = Field(min_length=1, max_length=500)


PARAMS: dict[str, type[BaseModel]] = {
    "refund": RefundParams,
    "create_invoice": CreateInvoiceParams,
    "send_invoice": SendInvoiceParams,
    "create_payment_link": CreatePaymentLinkParams,
}


class ActionError(Exception):
    """Base for confirm/cancel failures; messages are safe to show the owner."""


class ActionNotFound(ActionError):
    pass


class ActionStateError(ActionError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


async def propose(
    session: AsyncSession,
    *,
    owner_id: UUID,
    action_type: ActionType,
    params: BaseModel,
    preview: str,
    ttl_minutes: int,
    conversation_id: UUID | None = None,
    api_key_id: UUID | None = None,
    now: datetime | None = None,
) -> OwnerAction:
    expected = PARAMS[action_type]
    if not isinstance(params, expected):
        raise TypeError(f"{action_type} needs {expected.__name__}")
    now = now or _now()
    params_json = params.model_dump(mode="json")
    # Identical proposal still pending in the same place? Reuse it rather than stacking duplicate
    # cards (a model retrying the same call once produced four identical refund cards).
    existing = (
        (
            await session.execute(
                select(OwnerAction).where(
                    OwnerAction.owner_id == owner_id,
                    OwnerAction.action_type == action_type,
                    OwnerAction.status == "proposed",
                    OwnerAction.expires_at > now,
                    OwnerAction.params == params_json,
                    OwnerAction.conversation_id.is_(None)
                    if conversation_id is None
                    else OwnerAction.conversation_id == conversation_id,
                    OwnerAction.api_key_id.is_(None)
                    if api_key_id is None
                    else OwnerAction.api_key_id == api_key_id,
                )
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        existing.reused = True  # transient flag for callers; not a column
        return existing
    action = OwnerAction(
        owner_id=owner_id,
        conversation_id=conversation_id,
        api_key_id=api_key_id,
        action_type=action_type,
        params=params_json,
        preview=preview,
        status="proposed",
        expires_at=now + timedelta(minutes=ttl_minutes),
    )
    session.add(action)
    await session.flush()
    await audit.record(
        session,
        actor_type="owner_mcp" if api_key_id else "owner",
        actor_id=str(api_key_id or owner_id),
        action="action_proposed",
        target=str(action.id),
        payload={"action_type": action_type, "params": action.params},
    )
    return action


async def _load_for_decision(
    session: AsyncSession, action_id: UUID, owner_id: UUID, api_key_id: UUID | None
) -> OwnerAction:
    action = (
        await session.execute(
            select(OwnerAction).where(OwnerAction.id == action_id).with_for_update()
        )
    ).scalar_one_or_none()
    # A proposal can only be decided on the surface (and by the key) that created it. Anything
    # else looks exactly like "not found" so keys can't probe each other's proposals.
    if action is None or action.owner_id != owner_id or action.api_key_id != api_key_id:
        raise ActionNotFound("No such pending action.")
    return action


async def confirm(
    session: AsyncSession,
    gateway: OwnerStripeGateway,
    *,
    action_id: UUID,
    owner_id: UUID,
    api_key_id: UUID | None = None,
    now: datetime | None = None,
) -> OwnerAction:
    """Confirm and execute. Returns the action as `executed`, `failed`, or `expired`.

    Expiry and execution failures are returned, not raised, so the caller's transaction commits
    the new status (raising would roll it back and leave a stale `proposed` row)."""
    now = now or _now()
    action = await _load_for_decision(session, action_id, owner_id, api_key_id)
    if action.status != "proposed":
        raise ActionStateError(f"This action is already {action.status}.")
    if action.expires_at <= now:
        action.status = "expired"
        await session.flush()
        return action

    action.status = "confirmed"
    action.confirmed_at = now
    await session.flush()

    actor_type = "owner_mcp" if api_key_id else "owner"
    try:
        action.stripe_object_id = await _execute(gateway, action)
    except Exception as exc:  # surfaced to the owner; Stripe errors carry readable messages
        action.status = "failed"
        action.error = str(exc)[:500]
        await session.flush()
        await audit.record(
            session,
            actor_type=actor_type,
            actor_id=str(api_key_id or owner_id),
            action="action_failed",
            target=str(action.id),
            payload={"error": action.error},
        )
        return action

    action.status = "executed"
    action.executed_at = now
    await session.flush()
    await audit.record(
        session,
        actor_type=actor_type,
        actor_id=str(api_key_id or owner_id),
        action="action_executed",
        target=str(action.id),
        payload={"action_type": action.action_type, "stripe_object_id": action.stripe_object_id},
    )
    return action


async def cancel(
    session: AsyncSession,
    *,
    action_id: UUID,
    owner_id: UUID,
    api_key_id: UUID | None = None,
) -> OwnerAction:
    action = await _load_for_decision(session, action_id, owner_id, api_key_id)
    if action.status != "proposed":
        raise ActionStateError(f"This action is already {action.status}.")
    action.status = "cancelled"
    await session.flush()
    return action


async def _execute(gateway: OwnerStripeGateway, action: OwnerAction) -> str:
    key = str(action.idempotency_key)
    params: dict[str, Any] = action.params
    match action.action_type:
        case "refund":
            p = RefundParams.model_validate(params)
            return (
                await gateway.refund(charge_id=p.charge_id, amount=p.amount, idempotency_key=key)
            ).id
        case "create_invoice":
            p = CreateInvoiceParams.model_validate(params)
            return (
                await gateway.create_invoice(
                    customer_id=p.customer_id,
                    amount=p.amount,
                    currency=p.currency,
                    description=p.description,
                    due_date=p.due_date,
                    idempotency_key=key,
                )
            ).id
        case "send_invoice":
            p = SendInvoiceParams.model_validate(params)
            return (await gateway.send_invoice(invoice_id=p.invoice_id, idempotency_key=key)).id
        case "create_payment_link":
            p = CreatePaymentLinkParams.model_validate(params)
            return (
                await gateway.create_payment_link(
                    amount=p.amount,
                    currency=p.currency,
                    description=p.description,
                    idempotency_key=key,
                )
            ).id
    raise ActionStateError(f"Unknown action type {action.action_type}")  # pragma: no cover
