"""Telegram bot worker entrypoint (`python -m payments_assistant.bot`): long polling.

Connects to Postgres as the `bot` role (DATABASE_URL set per service in compose), so every
customer query is subject to RLS.
"""

import asyncio
import logging

from telegram import Update

from payments_assistant.bot.service import BotState
from payments_assistant.bot.telegram_app import build_application
from payments_assistant.core.config import get_settings
from payments_assistant.core.db import make_engine, make_sessionmaker
from payments_assistant.core.llm import get_chat_model
from payments_assistant.core.stripe_live import LiveCustomerGateway, make_client

logger = logging.getLogger("payments_assistant.bot")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # don't log the token-bearing URLs
    settings = get_settings()
    if not settings.telegram_bot_token:
        logger.error("TELEGRAM_BOT_TOKEN is not set; the bot is idle. Set it in .env and restart.")
        asyncio.run(asyncio.Event().wait())
        return

    client = make_client(settings)
    engine = make_engine(settings.database_url)
    state = BotState(
        settings=settings,
        sessionmaker=make_sessionmaker(engine),
        customer_gateway=lambda cus: LiveCustomerGateway(client, cus),
        model=lambda: get_chat_model(settings),
    )
    application = build_application(state, settings.telegram_bot_token)
    logger.info(
        "starting Telegram long polling (invite links use @%s)",
        settings.telegram_bot_username or "?",
    )
    # run_polling deletes any webhook first and manages its own event loop.
    application.run_polling(allowed_updates=[Update.MESSAGE], drop_pending_updates=False)


if __name__ == "__main__":
    main()
