"""Tests for playbook integration in EmailProcessor."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def processor():
    from orchestrator.handlers.email_processor import EmailProcessor
    db = AsyncMock()
    qdrant = MagicMock()
    qdrant.is_connected.return_value = False
    llm = AsyncMock()
    gmail = AsyncMock()
    telegram = AsyncMock()
    playbook_svc = AsyncMock()

    proc = EmailProcessor(db, qdrant, llm, gmail, telegram, playbook_service=playbook_svc)
    return proc


@pytest.mark.asyncio
async def test_playbook_match_auto_respond(processor):
    """When playbook matches with auto_respond, should send reply and notify."""
    processor.gmail.get_email.return_value = {
        "id": "em1", "from": "client@test.com", "from_email": "client@test.com",
        "from_name": "Client", "subject": "Segunda via boleto", "body": "Preciso da segunda via",
        "body_clean": "", "attachments": [], "threadId": "t1", "date": "2026-04-13",
    }
    processor.db.get_account.return_value = {"id": 1}
    processor.db.get_account_config.return_value = {"vips": [], "telegram_topic": 11}
    processor.llm.classify_email.return_value = {"prioridade": "Média", "importante": True, "confianca": 0.8, "categoria": "financeiro"}
    processor.llm.summarize_email.return_value = {"resumo": "Cliente pede segunda via de boleto"}
    processor.llm.decide_action.return_value = {"acao": "notificar", "account": "u@t.com"}

    # Playbook matches
    processor.playbook_service.match.return_value = {
        "playbook_id": 1,
        "template": "Prezado {nome_contato}, segue segunda via.",
        "trigger": "segunda via boleto",
        "auto_respond": True,
        "company": {"company_name": "CodeWave", "tone": "formal", "signature": "Att"},
    }
    processor.playbook_service.generate_response.return_value = "Prezado Client, segue segunda via do boleto."

    processor.telegram.send_email_notification.return_value = 100
    processor.db.log_decision.return_value = 1

    result = await processor.process_email("em1", "u@t.com")
    assert result["status"] == "success"
    assert result.get("playbook_matched") is True
    assert result.get("playbook_id") == 1
    # Should have sent reply via Gmail
    processor.gmail.send_reply.assert_called_once()


@pytest.mark.asyncio
async def test_playbook_no_match_normal_flow(processor):
    """When no playbook matches, normal pipeline continues."""
    processor.gmail.get_email.return_value = {
        "id": "em2", "from": "person@test.com", "from_email": "person@test.com",
        "from_name": "Person", "subject": "Hello", "body": "Hello there",
        "body_clean": "", "attachments": [], "threadId": "t2", "date": "2026-04-13",
    }
    processor.db.get_account.return_value = {"id": 1}
    processor.db.get_account_config.return_value = {"vips": [], "telegram_topic": 11}
    processor.llm.classify_email.return_value = {"prioridade": "Baixa", "importante": True, "confianca": 0.7, "categoria": "outro"}
    processor.llm.summarize_email.return_value = {"resumo": "Greeting"}
    processor.llm.decide_action.return_value = {"acao": "notificar", "account": "u@t.com"}
    processor.playbook_service.match.return_value = None
    processor.telegram.send_email_notification.return_value = 100
    processor.db.log_decision.return_value = 1

    result = await processor.process_email("em2", "u@t.com")
    assert result["status"] == "success"
    assert result.get("playbook_matched") is not True
    processor.gmail.send_reply.assert_not_called()
