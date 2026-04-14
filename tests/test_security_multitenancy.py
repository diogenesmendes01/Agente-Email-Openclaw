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


# ── Topic-scoped pending actions ──


@pytest.mark.asyncio
async def test_pending_by_chat_passes_topic_id():
    """get_pending_by_chat should receive topic_id when message has message_thread_id."""
    from orchestrator.handlers.telegram_callbacks import handle_text_message
    services = _make_services(allowed_user_ids={42})
    services["db"].get_pending_by_chat.return_value = None
    msg = {"chat": {"id": 100}, "message_thread_id": 555, "from": {"id": 42}, "text": "test"}
    await handle_text_message(msg, services)
    calls = services["db"].get_pending_by_chat.call_args_list
    for call in calls:
        assert call.kwargs.get("topic_id") == 555


# ── send_text thread routing ──


@pytest.mark.asyncio
async def test_command_sends_to_correct_topic():
    """Commands in topic groups should send replies to the same topic."""
    from orchestrator.handlers.telegram_commands import handle_command
    services = _make_services()
    services["db"].get_account_by_topic.return_value = {"id": 1}
    msg = {
        "chat": {"id": 100},
        "message_thread_id": 555,
        "from": {"id": 42},
        "text": "/config_identidade",
    }
    await handle_command(msg, services)
    call_kwargs = services["telegram"].send_text.call_args
    assert call_kwargs.kwargs.get("thread_id") == 555 or call_kwargs[1].get("thread_id") == 555


@pytest.mark.asyncio
async def test_command_no_thread_id_in_private_chat():
    """In a private chat (no message_thread_id), thread_id should be None."""
    from orchestrator.handlers.telegram_commands import handle_command
    services = _make_services()
    services["db"].get_account_by_topic.return_value = {"id": 1}
    msg = {"chat": {"id": 100}, "from": {"id": 42}, "text": "/config_identidade"}
    await handle_command(msg, services)
    call_kwargs = services["telegram"].send_text.call_args
    thread_id = call_kwargs.kwargs.get("thread_id") if call_kwargs.kwargs else call_kwargs[1].get("thread_id", None) if len(call_kwargs) > 1 else None
    assert thread_id is None


# ── Custom reply: topic_id and waiting_instruction ──


@pytest.mark.asyncio
async def test_custom_reply_passes_topic_id_to_generate():
    """generate_reply should receive topic_id so the draft lands in the correct topic."""
    from orchestrator.handlers.telegram_callbacks import handle_text_message
    services = _make_services(allowed_user_ids={42})
    pending_record = {
        "id": 10, "email_id": "email1", "account_id": 1, "topic_id": 555,
        "state": '{"waiting_instruction": true, "account": "test@gmail.com", "original_text": "..."}',
    }
    # Config types return None, custom_reply returns the pending
    async def mock_get_pending(chat_id, action_type, actor_id=None, topic_id=None):
        if action_type == "custom_reply":
            return pending_record
        return None
    services["db"].get_pending_by_chat.side_effect = mock_get_pending
    services["llm"].generate_custom_reply.return_value = "Draft reply text"
    services["db"].update_pending_state.return_value = None

    msg = {"chat": {"id": 100}, "message_thread_id": 555, "from": {"id": 42}, "text": "diz que entrego sexta"}
    await handle_text_message(msg, services)

    # send_text should have been called with thread_id=555
    send_call = services["telegram"].send_text.call_args
    assert send_call.kwargs.get("thread_id") == 555


@pytest.mark.asyncio
async def test_generate_reply_clears_waiting_instruction():
    """After generating a draft, waiting_instruction should be set to False."""
    from orchestrator.actions.reply import generate_reply
    from unittest.mock import AsyncMock

    db = AsyncMock()
    db.update_pending_state.return_value = None
    llm = AsyncMock()
    llm.generate_custom_reply.return_value = "Draft text"
    tg = AsyncMock()

    pending = {
        "id": 10, "state": '{"waiting_instruction": true, "original_text": "email body", "account": "a@b.com"}',
    }
    ctx = {
        "email_id": "email1", "account": "a@b.com", "chat_id": 100, "topic_id": 555,
        "instruction": "diz que entrego sexta", "pending": pending,
        "db": db, "gmail": AsyncMock(), "telegram": tg, "llm": llm,
    }
    result = await generate_reply(ctx)
    assert result == "Draft text"

    # Check that update_pending_state was called with waiting_instruction=False
    state_arg = db.update_pending_state.call_args[0][1]
    assert state_arg["waiting_instruction"] is False
    assert state_arg["last_reply"] == "Draft text"


@pytest.mark.asyncio
async def test_text_message_ignored_when_not_waiting_instruction():
    """After draft is generated, text messages should NOT be treated as new instructions."""
    from orchestrator.handlers.telegram_callbacks import handle_text_message
    services = _make_services(allowed_user_ids={42})
    pending_record = {
        "id": 10, "email_id": "email1", "account_id": 1, "topic_id": 555,
        "state": '{"waiting_instruction": false, "account": "a@b.com", "last_reply": "Draft"}',
    }
    async def mock_get_pending(chat_id, action_type, actor_id=None, topic_id=None):
        if action_type == "custom_reply":
            return pending_record
        return None
    services["db"].get_pending_by_chat.side_effect = mock_get_pending

    msg = {"chat": {"id": 100}, "message_thread_id": 555, "from": {"id": 42}, "text": "random message"}
    await handle_text_message(msg, services)

    # generate_custom_reply should NOT have been called
    services["llm"].generate_custom_reply.assert_not_called()
