"""Telegram handlers with fake Update/context objects and a stubbed service (no network)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest

from payments_assistant.bot import service, telegram_app
from payments_assistant.bot.telegram_app import STATE_KEY


def make(text="hello", args=None, chat_type="private"):
    bot = MagicMock()
    bot.send_message = AsyncMock()
    bot.send_chat_action = AsyncMock()
    state = object()
    context = SimpleNamespace(
        bot=bot, args=args or [], application=SimpleNamespace(bot_data={STATE_KEY: state})
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42, username="maya"),
        effective_chat=SimpleNamespace(id=4242, type=chat_type),
        effective_message=SimpleNamespace(text=text),
    )
    return update, context, bot, state


async def test_start_passes_payload(monkeypatch):
    update, context, bot, state = make(args=["abc"])
    handle = AsyncMock(return_value="Hi **Maya**!")
    monkeypatch.setattr(service, "handle_start", handle)
    await telegram_app.on_start(update, context)
    handle.assert_awaited_once_with(
        state, telegram_user_id=42, chat_id=4242, username="maya", payload="abc"
    )
    bot.send_message.assert_awaited_once_with(4242, "Hi <b>Maya</b>!", parse_mode=ParseMode.HTML)


async def test_start_without_payload(monkeypatch):
    update, context, _, _ = make(args=[])
    handle = AsyncMock(return_value="hi")
    monkeypatch.setattr(service, "handle_start", handle)
    await telegram_app.on_start(update, context)
    assert handle.await_args.kwargs["payload"] is None


async def test_text_calls_service_and_replies_html(monkeypatch):
    update, context, bot, state = make(text="what do I owe?")
    handle = AsyncMock(return_value="You owe **$180.00**.")
    monkeypatch.setattr(service, "handle_text", handle)
    await telegram_app.on_text(update, context)
    handle.assert_awaited_once_with(
        state, telegram_user_id=42, chat_id=4242, text_in="what do I owe?"
    )
    bot.send_message.assert_awaited_once_with(
        4242, "You owe <b>$180.00</b>.", parse_mode=ParseMode.HTML
    )
    bot.send_chat_action.assert_awaited_with(4242, ChatAction.TYPING)


async def test_service_exception_sends_fallback_without_internals(monkeypatch):
    update, context, bot, _ = make()
    monkeypatch.setattr(
        service, "handle_text", AsyncMock(side_effect=RuntimeError("db password=hunter2"))
    )
    await telegram_app.on_text(update, context)
    sent = bot.send_message.await_args.args[1]
    assert sent == service.FALLBACK_REPLY
    assert "hunter2" not in sent and "Traceback" not in sent


async def test_bad_html_falls_back_to_plain_text(monkeypatch):
    update, context, bot, _ = make()
    bot.send_message = AsyncMock(side_effect=[BadRequest("can't parse entities"), None])
    monkeypatch.setattr(service, "handle_text", AsyncMock(return_value="**odd** <markup>"))
    await telegram_app.on_text(update, context)
    first, second = bot.send_message.await_args_list
    assert first.kwargs["parse_mode"] == ParseMode.HTML
    assert second.args == (4242, "**odd** <markup>") and "parse_mode" not in second.kwargs


async def test_logout_and_help(monkeypatch):
    update, context, bot, state = make()
    handle = AsyncMock(return_value=service.LOGGED_OUT_REPLY)
    monkeypatch.setattr(service, "handle_logout", handle)
    await telegram_app.on_logout(update, context)
    handle.assert_awaited_once_with(state, telegram_user_id=42)
    await telegram_app.on_help(update, context)
    assert bot.send_message.await_count == 2


def test_application_only_handles_private_chats():
    import datetime as dt

    from telegram import Chat, Message, Update, User

    app = telegram_app.build_application(MagicMock(), "123:ABC")
    assert app.bot_data[STATE_KEY] is not None

    def update_in(chat_type: str) -> Update:
        u = Update(
            1,
            message=Message(
                1,
                dt.datetime.now(dt.UTC),
                Chat(9, chat_type),
                from_user=User(42, "M", False),
                text="hello",
            ),
        )
        u.set_bot(app.bot)
        u.message.set_bot(app.bot)
        return u

    handlers = app.handlers[0]
    assert any(h.check_update(update_in(Chat.PRIVATE)) for h in handlers)
    for kind in (Chat.GROUP, Chat.SUPERGROUP, Chat.CHANNEL):
        assert not any(h.check_update(update_in(kind)) for h in handlers)


@pytest.mark.parametrize("cmd", ["/start abc", "/logout", "/help"])
def test_commands_routed_not_to_text_handler(cmd):
    import datetime as dt

    from telegram import Chat, Message, MessageEntity, Update, User

    app = telegram_app.build_application(MagicMock(), "123:ABC")
    name = cmd.split()[0]
    u = Update(
        1,
        message=Message(
            1,
            dt.datetime.now(dt.UTC),
            Chat(9, Chat.PRIVATE),
            from_user=User(42, "M", False),
            text=cmd,
            entities=[MessageEntity(MessageEntity.BOT_COMMAND, 0, len(name))],
        ),
    )
    # CommandHandler needs bot.username (normally fetched from Telegram on initialize()).
    stub_bot = MagicMock(username="penny_bot")
    u.set_bot(stub_bot)
    u.message.set_bot(stub_bot)
    matched = [h for h in app.handlers[0] if h.check_update(u)]
    assert len(matched) == 1 and matched[0].callback.__name__ != "on_text"


def test_username_mismatch_detection():
    from payments_assistant.bot.telegram_app import username_mismatch

    assert username_mismatch("ledger_payments_bot", "ledger_payments_bot") is None
    assert username_mismatch("@Ledger_Payments_Bot", "ledger_payments_bot") is None
    assert username_mismatch("anything", None) is None
    warning = username_mismatch("edger_payments_bot", "ledger_payments_bot")
    assert warning and "TELEGRAM_BOT_USERNAME=ledger_payments_bot" in warning
