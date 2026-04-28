"""Tests for telegram_callbacks router."""
import asyncio
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
    await asyncio.sleep(0)  # yield to event loop so create_task can run
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
    await asyncio.sleep(0)  # yield to event loop so create_task can run
    services["telegram"].answer_callback.assert_called_once()


@pytest.mark.asyncio
async def test_text_message_triggers_custom_reply():
    """Text message while custom_reply is pending should generate reply."""
    from orchestrator.handlers.telegram_callbacks import handle_text_message
    services = _make_services()
    services["db"].get_pending_by_chat.side_effect = [
        None,  # No config_identidade pending
        None,  # No config_playbook pending
        None,  # No config_documentos pending
        None,  # No config_prompt pending
        None,  # No prompt_reset pending
        {"id": 1, "email_id": "em_1", "account_id": 1, "state": '{"original_text": "email body", "account": "u@t.com"}'},
    ]
    services["llm"].generate_custom_reply.return_value = "Draft reply"
    services["db"].update_pending_state.return_value = None

    msg = {"chat": {"id": 100}, "text": "diz que entrego na sexta"}
    await handle_text_message(msg, services)
    services["llm"].generate_custom_reply.assert_called_once()


@pytest.mark.asyncio
async def test_text_message_triggers_task_creation():
    """Text message while create_task is pending should create task."""
    from orchestrator.handlers.telegram_callbacks import handle_text_message
    services = _make_services()
    services["db"].get_pending_by_chat.side_effect = [
        None,  # No config_identidade pending
        None,  # No config_playbook pending
        None,  # No config_documentos pending
        None,  # No config_prompt pending
        None,  # No prompt_reset pending
        None,  # No custom_reply pending
        {"id": 2, "email_id": "em_2", "account_id": 1,
         "state": '{"account": "u@t.com", "subject": "Subj", "urgency": "high"}'},
    ]
    services["db"].create_task.return_value = 1

    msg = {"chat": {"id": 100}, "text": "Ligar para o cliente"}
    await handle_text_message(msg, services)
    services["db"].create_task.assert_called_once()


@pytest.mark.asyncio
async def test_handle_callback_notifies_user_on_error():
    """If the handler raises, the user gets an error message instead of silence."""
    from orchestrator.handlers.telegram_callbacks import handle_callback
    services = _make_services()
    # Force an error in the inner handler
    services["db"].get_pending_action = AsyncMock(side_effect=RuntimeError("DB down"))

    # set_urgency calls db.get_pending_action immediately → triggers the mock error
    callback_query = {
        "id": "cq1",
        "data": "set_urgency:high:email_1",
        "from": {"id": 99},
        "message": {"chat": {"id": 100}, "message_id": 1, "text": "body"},
    }
    # Should NOT raise — error is caught and user is notified
    await handle_callback(callback_query, services)
    services["telegram"].send_text.assert_called_once()
    call_args = services["telegram"].send_text.call_args[0]
    assert "❌" in call_args[1]


@pytest.mark.asyncio
async def test_handle_text_message_notifies_user_on_error():
    """If the text handler raises, the user gets an error message instead of silence."""
    from orchestrator.handlers.telegram_callbacks import handle_text_message
    services = _make_services()
    services["db"].get_pending_by_chat = AsyncMock(side_effect=RuntimeError("DB down"))

    msg = {"chat": {"id": 100}, "text": "alguma mensagem", "from": {"id": 99}}
    # Should NOT raise
    await handle_text_message(msg, services)
    services["telegram"].send_text.assert_called_once()
    call_args = services["telegram"].send_text.call_args[0]
    assert "❌" in call_args[1]
