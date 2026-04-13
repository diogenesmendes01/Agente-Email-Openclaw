"""Tests for telegram_callbacks router."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_services():
    return {
        "db": AsyncMock(),
        "gmail": AsyncMock(),
        "telegram": AsyncMock(),
        "llm": AsyncMock(),
    }


def _make_callback(action, email_id="em_123", account="user@test.com"):
    """Build a Telegram callback_query dict."""
    return {
        "id": "cb_1",
        "data": f"{action}:{email_id}:{account}",
        "from": {"id": 42},
        "message": {
            "message_id": 200,
            "chat": {"id": 100},
            "text": "🔴 CRITICAL │ 💰 Financeiro │ 95%\n📨 sender@test.com\n📋 Test Subject",
        },
    }


@pytest.mark.asyncio
async def test_route_archive_shows_confirmation():
    from orchestrator.handlers.telegram_callbacks import handle_callback
    services = _make_services()
    services["db"].get_account.return_value = {"id": 1}
    cb = _make_callback("archive")
    await handle_callback(cb, services)
    services["telegram"].answer_callback.assert_called_once()
    services["telegram"].edit_message.assert_called_once()


@pytest.mark.asyncio
async def test_route_confirm_archive_executes():
    from orchestrator.handlers.telegram_callbacks import handle_callback
    services = _make_services()
    services["db"].get_account.return_value = {"id": 1}
    services["db"].get_pending_action.return_value = {
        "id": 1, "account_id": 1, "state": '{"account": "user@test.com", "sender": "s@t.com", "original_text": "orig"}',
        "email_id": "em_123", "chat_id": 100, "message_id": 200,
    }
    services["gmail"].archive_email.return_value = True
    cb = _make_callback("confirm_archive")
    await handle_callback(cb, services)
    services["gmail"].archive_email.assert_called_once()


@pytest.mark.asyncio
async def test_route_reclassify_swaps_buttons():
    from orchestrator.handlers.telegram_callbacks import handle_callback
    services = _make_services()
    services["db"].get_account.return_value = {"id": 1}
    services["db"].create_pending_action.return_value = 1
    cb = _make_callback("reclassify")
    await handle_callback(cb, services)
    services["telegram"].edit_reply_markup.assert_called_once()


@pytest.mark.asyncio
async def test_route_set_urgency_completes():
    from orchestrator.handlers.telegram_callbacks import handle_callback
    services = _make_services()
    services["db"].get_account.return_value = {"id": 1}
    services["db"].get_pending_action.return_value = {
        "id": 1, "account_id": 1,
        "state": '{"original_urgency": "low", "keywords": [], "original_text": "orig", "account": "user@test.com"}',
        "email_id": "em_123", "chat_id": 100, "message_id": 200,
    }
    cb = _make_callback("set_urgency")
    cb["data"] = "set_urgency:high:em_123"
    await handle_callback(cb, services)
    services["db"].save_feedback.assert_called_once()


@pytest.mark.asyncio
async def test_unknown_action_logged():
    from orchestrator.handlers.telegram_callbacks import handle_callback
    services = _make_services()
    cb = _make_callback("unknown_action")
    await handle_callback(cb, services)
    services["telegram"].answer_callback.assert_called_once()
