"""Telegram bot logic, independent of the Telegram library (the adapter is bot/telegram_app.py).

Every customer data access runs as the `bot` Postgres role. Identity comes ONLY from the
SECURITY DEFINER functions (redeem/resolve/revoke); all reads and writes of customer rows happen
inside `customer_scope`, and Stripe access goes through a gateway bound to that customer.
"""

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from langchain_core.language_models import BaseChatModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from payments_assistant.core.agents.customer_agent import run_customer_turn
from payments_assistant.core.config import Settings
from payments_assistant.core.models import Conversation, CustomerAccount
from payments_assistant.core.scoping import customer_scope
from payments_assistant.core.services import audit
from payments_assistant.core.services import conversations as convs
from payments_assistant.core.stripe_gateway import CustomerStripeGateway
from payments_assistant.core.tools import ToolContext

logger = logging.getLogger(__name__)

UNLINKED_REPLY = (
    "Hi! I'm Penny, the payments assistant. I don't know who you are yet. Ask the business to "
    "send you your personal invite link, then tap it to connect your account."
)
BAD_INVITE_REPLY = (
    "That link has expired or was already used. Ask the business for a fresh invite link."
)
LOGGED_OUT_REPLY = "Done. This Telegram account is no longer linked. Tap a new invite link anytime."
NOT_LINKED_LOGOUT_REPLY = (
    "This Telegram account isn't linked to anything, so there's nothing to do."
)
FALLBACK_REPLY = "Sorry, I couldn't put an answer together just now. Please try again in a moment."
HELP_REPLY = (
    "I can tell you what you owe, show your invoices and recent payments, and send you a secure "
    "link to pay. Just ask, e.g. “what do I owe?” or “pay my invoice”.\n\n"
    "/logout unlinks this Telegram account."
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class BotState:
    settings: Settings
    sessionmaker: async_sessionmaker[AsyncSession]  # bound to the `bot` role
    customer_gateway: Callable[[str], CustomerStripeGateway]  # stripe customer id -> gateway
    model: Callable[[], BaseChatModel]
    clock: Callable[[], datetime] = field(default=_utcnow)


def hash_invite_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def _account(session: AsyncSession, account_id: UUID) -> CustomerAccount | None:
    return await session.get(CustomerAccount, account_id)


async def handle_start(
    state: BotState,
    *,
    telegram_user_id: int,
    chat_id: int,
    username: str | None,
    payload: str | None,
) -> str:
    """`/start` or `/start <invite token>` (the deep link)."""
    if not payload:
        linked = await resolve(state, telegram_user_id)
        if linked is None:
            return UNLINKED_REPLY
        return await _welcome(state, linked)
    async with state.sessionmaker() as session, session.begin():
        account_id = (
            await session.execute(
                text("select redeem_customer_invite(:h, :u, :c, :n)"),
                {
                    "h": hash_invite_token(payload.strip()),
                    "u": telegram_user_id,
                    "c": chat_id,
                    "n": username,
                },
            )
        ).scalar_one()
    if account_id is None:
        return BAD_INVITE_REPLY
    async with state.sessionmaker() as session, customer_scope(session, account_id):
        await audit.record(
            session,
            actor_type="customer",
            actor_id=str(account_id),
            customer_account_id=account_id,
            action="telegram_linked",
            target=str(telegram_user_id),
        )
    return await _welcome(state, account_id)


async def _welcome(state: BotState, account_id: UUID) -> str:
    async with state.sessionmaker() as session, customer_scope(session, account_id):
        account = await _account(session, account_id)
    name = account.display_name.split()[0] if account and account.display_name else "there"
    return (
        f"Hi {name}! You're connected. I can tell you what you owe and send you a secure link "
        "to pay. Try “what do I owe?”"
    )


async def resolve(state: BotState, telegram_user_id: int) -> UUID | None:
    async with state.sessionmaker() as session, session.begin():
        return (
            await session.execute(
                text("select resolve_telegram_identity(:u)"), {"u": telegram_user_id}
            )
        ).scalar_one()


async def handle_logout(state: BotState, *, telegram_user_id: int) -> str:
    async with state.sessionmaker() as session, session.begin():
        revoked = (
            await session.execute(
                text("select revoke_telegram_identity(:u)"), {"u": telegram_user_id}
            )
        ).scalar_one()
    return LOGGED_OUT_REPLY if revoked else NOT_LINKED_LOGOUT_REPLY


async def handle_text(state: BotState, *, telegram_user_id: int, chat_id: int, text_in: str) -> str:
    """A free-text message: resolve identity, then run one customer-agent turn in scope.

    Unlinked users get a fixed reply: no LLM call, nothing stored."""
    account_id = await resolve(state, telegram_user_id)
    if account_id is None:
        return UNLINKED_REPLY

    now = state.clock()
    async with state.sessionmaker() as session, customer_scope(session, account_id):
        account = await _account(session, account_id)
        if account is None:  # pragma: no cover - resolve() already checked status
            return UNLINKED_REPLY
        conv = await _open_conversation(session, account_id, chat_id, now)
        await convs.append_message(session, conv, role="user", content=text_in, now=now)
        history = convs.history_for_model(await convs.list_messages(session, conv.id))

        ctx = ToolContext(
            session=session,
            settings=state.settings,
            now=now,
            customer_account_id=account_id,
            customer_gateway=state.customer_gateway(account.stripe_customer_id),
            conversation_id=conv.id,
        )
        end = await run_customer_turn(
            state.model(),
            ctx=ctx,
            history=history,
            customer_name=account.display_name,
            settings=state.settings,
        )
        for record in end.tool_records:
            await convs.append_message(
                session,
                conv,
                role="tool",
                content="",
                now=now,
                tool_name=record.tool,
                tool_payload=record.model_dump(mode="json"),
            )
        reply = end.text.strip() or FALLBACK_REPLY
        await convs.append_message(
            session,
            conv,
            role="assistant",
            content=reply,
            now=now,
            status="complete" if end.text.strip() else "error",
            llm_model=state.settings.llm_model,
            input_tokens=end.input_tokens,
            output_tokens=end.output_tokens,
        )
    return reply


async def _open_conversation(
    session: AsyncSession, account_id: UUID, chat_id: int, now: datetime
) -> Conversation:
    """One ongoing conversation per customer per Telegram chat (RLS limits this to the customer)."""
    conv = (
        await session.execute(
            select(Conversation)
            .where(
                Conversation.channel == "customer_telegram",
                Conversation.customer_account_id == account_id,
                Conversation.external_chat_id == str(chat_id),
                Conversation.status == "open",
            )
            .order_by(Conversation.last_message_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if conv is None:
        conv = Conversation(
            channel="customer_telegram",
            customer_account_id=account_id,
            external_chat_id=str(chat_id),
            created_at=now,
            last_message_at=now,
        )
        session.add(conv)
        await session.flush()
    return conv
