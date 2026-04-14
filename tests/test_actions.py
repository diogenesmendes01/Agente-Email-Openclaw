"""Tests for action modules."""
import pytest
from unittest.mock import AsyncMock, MagicMock


def _make_ctx(**overrides):
    """Build a standard action context dict."""
    ctx = {
        "email_id": "em_123",
        "account": "user@test.com",
        "account_id": 1,
        "sender": "sender@test.com",
        "subject": "Test Subject",
        "chat_id": 100,
        "message_id": 200,
        "original_text": "original msg",
        "actor_id": 42,
        "db": AsyncMock(),
        "gmail": AsyncMock(),
        "telegram": AsyncMock(),
        "llm": AsyncMock(),
    }
    ctx.update(overrides)
    return ctx


@pytest.mark.asyncio
async def test_archive_execute():
    from orchestrator.actions.archive import execute
    ctx = _make_ctx()
    ctx["gmail"].archive_email.return_value = True
    status = await execute(ctx)
    assert "Arquivado" in status
    ctx["gmail"].archive_email.assert_called_once_with("em_123", "user@test.com")


@pytest.mark.asyncio
async def test_archive_failure():
    from orchestrator.actions.archive import execute
    ctx = _make_ctx()
    ctx["gmail"].archive_email.side_effect = Exception("Gmail error")
    status = await execute(ctx)
    assert "Erro" in status


@pytest.mark.asyncio
async def test_vip_execute():
    from orchestrator.actions.vip import execute
    ctx = _make_ctx()
    ctx["db"].add_vip.return_value = True
    status = await execute(ctx)
    assert "VIP" in status
    ctx["db"].add_vip.assert_called_once()


@pytest.mark.asyncio
async def test_silence_execute():
    from orchestrator.actions.silence import execute
    ctx = _make_ctx()
    ctx["db"].add_to_blacklist.return_value = True
    status = await execute(ctx)
    assert "Silenciado" in status


@pytest.mark.asyncio
async def test_spam_execute():
    from orchestrator.actions.spam import execute
    ctx = _make_ctx()
    ctx["gmail"].mark_as_spam.return_value = True
    status = await execute(ctx)
    assert "Spam" in status
    ctx["gmail"].mark_as_spam.assert_called_once()
    ctx["db"].add_to_blacklist.assert_called_once()


@pytest.mark.asyncio
async def test_task_execute():
    from orchestrator.actions.task import execute
    ctx = _make_ctx()
    ctx["db"].create_task.return_value = 1
    status = await execute(ctx)
    assert "Tarefa" in status or "tarefa" in status
    ctx["db"].create_task.assert_called_once()


# --- Task 4: Feedback + Reply tests ---

@pytest.mark.asyncio
async def test_feedback_start_reclassify():
    from orchestrator.actions.feedback import start_reclassify
    ctx = _make_ctx()
    ctx["db"].create_pending_action.return_value = 1
    result = await start_reclassify(ctx)
    assert result is True
    ctx["telegram"].edit_reply_markup.assert_called_once()


@pytest.mark.asyncio
async def test_feedback_complete_reclassify():
    from orchestrator.actions.feedback import complete_reclassify
    ctx = _make_ctx()
    ctx["new_urgency"] = "high"
    ctx["pending"] = {"id": 1, "state": '{"original_urgency": "low", "keywords": []}'}
    ctx["db"].save_feedback.return_value = True
    ctx["db"].delete_pending_action.return_value = None
    status = await complete_reclassify(ctx)
    assert "high" in status.lower() or "HIGH" in status


@pytest.mark.asyncio
async def test_reply_start():
    from orchestrator.actions.reply import start_reply
    ctx = _make_ctx()
    ctx["db"].create_pending_action.return_value = 1
    result = await start_reply(ctx)
    assert result is True
    ctx["telegram"].send_text.assert_called_once()


@pytest.mark.asyncio
async def test_reply_generate():
    from orchestrator.actions.reply import generate_reply
    ctx = _make_ctx()
    ctx["instruction"] = "diz que entrego na sexta"
    ctx["pending"] = {"id": 1, "state": '{"original_text": "email body"}'}
    ctx["llm"].generate_custom_reply.return_value = "Prezado, entregarei na sexta."
    ctx["db"].update_pending_state.return_value = None
    result = await generate_reply(ctx)
    assert result is not None


@pytest.mark.asyncio
async def test_reply_send_draft():
    from orchestrator.actions.reply import send_draft
    ctx = _make_ctx()
    ctx["pending"] = {"id": 1, "state": '{"last_reply": "Prezado...", "sender": "s@t.com"}'}
    ctx["gmail"].send_reply.return_value = True
    status = await send_draft(ctx)
    assert "Respondido" in status or "Enviado" in status or "✉️" in status
