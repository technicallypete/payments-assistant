"""Telegram bot worker entrypoint (`python -m payments_assistant.bot`). Stub until Phase 7."""

import asyncio
import logging

logger = logging.getLogger("payments_assistant.bot")


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logger.info("bot worker stub running; Telegram polling arrives in Phase 7")
    await asyncio.Event().wait()  # idle forever


if __name__ == "__main__":
    asyncio.run(main())
