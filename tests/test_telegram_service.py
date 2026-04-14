"""Tests for TelegramService helper methods."""
import os
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from orchestrator.services.telegram_service import TelegramService


@pytest.fixture
def tg_service():
    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_CHAT_ID": "-100123",
    }):
        return TelegramService()


@pytest.mark.asyncio
async def test_answer_callback(tg_service):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await tg_service.answer_callback("cb123", "Done!")
        assert result is True
        mock_client.post.assert_called_once()
        call_url = mock_client.post.call_args[0][0]
        assert "answerCallbackQuery" in call_url


@pytest.mark.asyncio
async def test_edit_reply_markup(tg_service):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        keyboard = {"inline_keyboard": [[{"text": "OK", "callback_data": "ok"}]]}
        result = await tg_service.edit_reply_markup(123, 456, keyboard)
        assert result is True


@pytest.mark.asyncio
async def test_delete_message(tg_service):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await tg_service.delete_message(123, 456)
        assert result is True


@pytest.mark.asyncio
async def test_set_webhook(tg_service):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True}
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await tg_service.set_webhook("https://example.com/telegram/callback", "secret123")
        assert result is True


@pytest.mark.asyncio
async def test_send_text(tg_service):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"result": {"message_id": 42}}
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await tg_service.send_text(100, "Hello!")
        assert result == 42
