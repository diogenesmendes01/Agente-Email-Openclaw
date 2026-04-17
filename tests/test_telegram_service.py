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


def test_acao_usuario_fallback_to_justificativa(tg_service):
    """When acao_usuario is missing/empty, AÇÃO NECESSÁRIA must show justificativa."""
    email = {"from": "x@y.com", "subject": "Urgente", "date": "2026-04-17 10:00"}
    classification = {"prioridade": "Alta", "importante": True, "categoria": "cliente", "confianca": 0.9}
    summary = {"resumo": "resumo"}

    # Case 1: acao_usuario missing, justificativa present
    action = {"justificativa": "Cliente VIP pediu retorno urgente"}
    msg = tg_service._format_message(email, classification, summary, action)
    assert "AÇÃO NECESSÁRIA" in msg
    assert "Cliente VIP pediu retorno urgente" in msg

    # Case 2: acao_usuario empty string, justificativa present
    action = {"acao_usuario": "", "justificativa": "Fallback ativado"}
    msg = tg_service._format_message(email, classification, summary, action)
    assert "Fallback ativado" in msg

    # Case 3: both missing -> default
    action = {}
    msg = tg_service._format_message(email, classification, summary, action)
    assert "Verificar e tomar ação necessária" in msg

    # Case 4: acao_usuario present -> takes precedence
    action = {"acao_usuario": "Responder antes de 18h", "justificativa": "não usado"}
    msg = tg_service._format_message(email, classification, summary, action)
    assert "Responder antes de 18h" in msg
    assert "não usado" not in msg


def test_rascunho_truncation_2000_chars(tg_service):
    """Rascunho longer than 2000 chars must be truncated with continuation marker."""
    email = {"from": "x@y.com", "subject": "Test", "date": "2026-04-17 10:00"}
    classification = {"prioridade": "Media", "categoria": "outro", "confianca": 0.5}
    summary = {"resumo": "r"}

    # Short draft: no marker
    short_draft = "Olá, obrigado pelo contato."
    action = {"rascunho_resposta": short_draft}
    msg = tg_service._format_message(email, classification, summary, action)
    assert short_draft in msg
    assert "rascunho continua" not in msg

    # Long draft: truncated + marker
    long_draft = "A" * 2500
    action = {"rascunho_resposta": long_draft}
    msg = tg_service._format_message(email, classification, summary, action)
    # First 2000 A's should be present; the 2500th should not
    assert "A" * 2000 in msg
    assert "A" * 2001 not in msg
    assert "…(rascunho continua — use Editar)" in msg

    # Exactly 2000: no marker
    exact_draft = "B" * 2000
    action = {"rascunho_resposta": exact_draft}
    msg = tg_service._format_message(email, classification, summary, action)
    assert "B" * 2000 in msg
    assert "rascunho continua" not in msg


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
