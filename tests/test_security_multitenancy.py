"""Tests for auth allowlist, topic routing, pending isolation, and playbook ownership."""
import pytest
from unittest.mock import AsyncMock


def _make_services(allowed_user_ids=None):
    return {
        "db": AsyncMock(),
        "telegram": AsyncMock(),
        "llm": AsyncMock(),
        "gmail": AsyncMock(),
        "allowed_user_ids": allowed_user_ids or set(),
    }


# ── Auth allowlist ──


@pytest.mark.asyncio
async def test_callback_blocked_for_unauthorized_user():
    """Callback from a user NOT in allowed_user_ids should be rejected."""
    from orchestrator.handlers.telegram_callbacks import handle_callback
    services = _make_services(allowed_user_ids={111, 222})
    callback = {
        "id": "cb1",
        "data": "archive:email1:test@gmail.com",
        "from": {"id": 999},  # not in allowlist
        "message": {"chat": {"id": 100}, "message_id": 1, "text": "..."},
    }
    await handle_callback(callback, services)
    services["telegram"].answer_callback.assert_called_once_with("cb1", "⛔ Acesso não autorizado")
    # No action should have been taken
    services["db"].create_pending_action.assert_not_called()


@pytest.mark.asyncio
async def test_callback_allowed_for_authorized_user():
    """Callback from an allowed user should proceed normally."""
    from orchestrator.handlers.telegram_callbacks import handle_callback
    services = _make_services(allowed_user_ids={42})
    services["db"].get_account.return_value = {"id": 1}
    callback = {
        "id": "cb1",
        "data": "archive:email1:test@gmail.com",
        "from": {"id": 42},
        "message": {"chat": {"id": 100}, "message_id": 1, "text": "test"},
    }
    await handle_callback(callback, services)
    # Should have proceeded to the confirmation step
    services["db"].create_pending_action.assert_called_once()


@pytest.mark.asyncio
async def test_text_message_blocked_for_unauthorized_user():
    """Text message from unauthorized user should be silently ignored."""
    from orchestrator.handlers.telegram_callbacks import handle_text_message
    services = _make_services(allowed_user_ids={111})
    msg = {"chat": {"id": 100}, "from": {"id": 999}, "text": "/config_identidade"}
    await handle_text_message(msg, services)
    services["telegram"].send_text.assert_not_called()


@pytest.mark.asyncio
async def test_text_message_allowed_for_authorized_user():
    """Text message from authorized user should be processed."""
    from orchestrator.handlers.telegram_callbacks import handle_text_message
    services = _make_services(allowed_user_ids={42})
    services["db"].get_account_by_topic.return_value = {"id": 1}
    msg = {"chat": {"id": 100}, "from": {"id": 42}, "text": "/config_identidade"}
    await handle_text_message(msg, services)
    services["telegram"].send_text.assert_called_once()


# ── Topic routing ──


@pytest.mark.asyncio
async def test_topic_routing_uses_message_thread_id():
    """In a topic group, get_account_by_topic should receive message_thread_id, not chat.id."""
    from orchestrator.handlers.telegram_commands import handle_command
    services = _make_services()
    services["db"].get_account_by_topic.return_value = {"id": 1}
    msg = {
        "chat": {"id": 100},
        "message_thread_id": 555,  # the real topic id
        "from": {"id": 42},
        "text": "/config_identidade",
    }
    await handle_command(msg, services)
    # Should resolve account by topic 555, not chat 100
    services["db"].get_account_by_topic.assert_called_once_with(555)


@pytest.mark.asyncio
async def test_topic_routing_fallback_to_chat_id():
    """Without message_thread_id, should fall back to chat.id."""
    from orchestrator.handlers.telegram_commands import handle_command
    services = _make_services()
    services["db"].get_account_by_topic.return_value = {"id": 1}
    msg = {"chat": {"id": 100}, "from": {"id": 42}, "text": "/config_identidade"}
    await handle_command(msg, services)
    services["db"].get_account_by_topic.assert_called_once_with(100)


# ── Pending action isolation ──


@pytest.mark.asyncio
async def test_pending_action_isolated_by_actor():
    """User B should not see User A's pending action in text message matching."""
    from orchestrator.handlers.telegram_callbacks import handle_text_message
    services = _make_services(allowed_user_ids={42, 99})
    # User 42 has a pending custom_reply, but user 99 sends a message
    services["db"].get_pending_by_chat.return_value = None  # no match for actor 99
    msg = {"chat": {"id": 100}, "from": {"id": 99}, "text": "some text"}
    await handle_text_message(msg, services)
    # get_pending_by_chat should have been called with actor_id=99
    calls = services["db"].get_pending_by_chat.call_args_list
    for call in calls:
        assert call.kwargs.get("actor_id") == 99


@pytest.mark.asyncio
async def test_confirm_action_isolated_by_actor():
    """Confirm callback should only find pending actions belonging to the same actor."""
    from orchestrator.handlers.telegram_callbacks import handle_callback
    services = _make_services(allowed_user_ids={42})
    services["db"].get_pending_action.return_value = None  # not found for this actor
    callback = {
        "id": "cb1",
        "data": "confirm_archive:email1:test@gmail.com",
        "from": {"id": 42},
        "message": {"chat": {"id": 100}, "message_id": 1, "text": "..."},
    }
    await handle_callback(callback, services)
    services["db"].get_pending_action.assert_called_once_with("email1", "archive", actor_id=42)
