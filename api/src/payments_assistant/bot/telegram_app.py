"""python-telegram-bot adapter over `bot.service` (long polling; private chats only).

Handlers are thin: pull ids out of the Update, call the service, send the reply. Any failure is
logged and answered with a generic message, so internals never reach a customer.
"""

import asyncio
import contextlib
import logging

from telegram import Bot, BotCommand, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from payments_assistant.bot import service
from payments_assistant.bot.formatting import split_message, to_telegram_html
from payments_assistant.bot.service import BotState

logger = logging.getLogger(__name__)

TYPING_INTERVAL_SECONDS = 4.0
STATE_KEY = "bot_state"

COMMANDS = [
    BotCommand("start", "Connect your account (use your invite link)"),
    BotCommand("help", "What I can do"),
    BotCommand("logout", "Unlink this Telegram account"),
]


async def send_reply(bot: Bot, chat_id: int, text: str) -> None:
    """Send as Telegram HTML, chunked; fall back to plain text if Telegram rejects the markup."""
    for chunk in split_message(text):
        try:
            await bot.send_message(chat_id, to_telegram_html(chunk), parse_mode=ParseMode.HTML)
        except BadRequest:
            logger.warning("Telegram rejected HTML; resending as plain text")
            await bot.send_message(chat_id, chunk)


async def notify(token: str, chat_id: int, text: str) -> None:
    """Push a message outside a conversation (e.g. the Stripe webhook's 'payment received')."""
    async with Bot(token) as bot:
        await send_reply(bot, chat_id, text)


async def _send_typing(bot: Bot, chat_id: int) -> None:
    with contextlib.suppress(Exception):
        await bot.send_chat_action(chat_id, ChatAction.TYPING)


async def _keep_typing(bot: Bot, chat_id: int) -> None:
    """Telegram shows "typing…" for ~5s per action; refresh it until the reply is ready."""
    while True:
        await asyncio.sleep(TYPING_INTERVAL_SECONDS)
        await _send_typing(bot, chat_id)


def _state(context: ContextTypes.DEFAULT_TYPE) -> BotState:
    return context.application.bot_data[STATE_KEY]


async def _safely(update: Update, context: ContextTypes.DEFAULT_TYPE, work) -> None:
    chat_id = update.effective_chat.id
    try:
        reply = await work()
    except Exception:
        logger.exception("bot handler failed")
        reply = service.FALLBACK_REPLY
    await send_reply(context.bot, chat_id, reply)


async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, chat = update.effective_user, update.effective_chat
    payload = context.args[0] if context.args else None
    await _safely(
        update,
        context,
        lambda: service.handle_start(
            _state(context),
            telegram_user_id=user.id,
            chat_id=chat.id,
            username=user.username,
            payload=payload,
        ),
    )


async def on_logout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await _safely(
        update,
        context,
        lambda: service.handle_logout(_state(context), telegram_user_id=user.id),
    )


async def on_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_reply(context.bot, update.effective_chat.id, service.HELP_REPLY)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, chat = update.effective_user, update.effective_chat
    await _send_typing(context.bot, chat.id)  # immediately, then every few seconds
    typing = asyncio.create_task(_keep_typing(context.bot, chat.id))
    try:
        await _safely(
            update,
            context,
            lambda: service.handle_text(
                _state(context),
                telegram_user_id=user.id,
                chat_id=chat.id,
                text_in=update.effective_message.text,
            ),
        )
    finally:
        typing.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await typing


async def _post_init(application: Application) -> None:
    with contextlib.suppress(Exception):
        await application.bot.set_my_commands(COMMANDS)


def build_application(state: BotState, token: str) -> Application:
    application = Application.builder().token(token).post_init(_post_init).build()
    application.bot_data[STATE_KEY] = state
    private = filters.ChatType.PRIVATE
    application.add_handler(CommandHandler("start", on_start, filters=private))
    application.add_handler(CommandHandler("logout", on_logout, filters=private))
    application.add_handler(CommandHandler("help", on_help, filters=private))
    application.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, on_text))
    return application
