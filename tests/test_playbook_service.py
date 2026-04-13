"""Tests for PlaybookService."""
import pytest
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_match_playbook_found():
    from orchestrator.services.playbook_service import PlaybookService
    db = AsyncMock()
    llm = AsyncMock()
    db.get_company_profile.return_value = {"id": 1, "company_name": "CodeWave", "tone": "formal", "signature": "Att, CodeWave"}
    db.get_playbooks.return_value = [
        {"id": 1, "trigger_description": "dúvida sobre boleto", "response_template": "Prezado {nome_contato}, segue segunda via.", "auto_respond": True},
        {"id": 2, "trigger_description": "cancelamento", "response_template": "Lamentamos...", "auto_respond": True},
    ]
    llm.match_playbook.return_value = {"matched_id": 1, "confidence": 0.9}

    svc = PlaybookService(db, llm)
    result = await svc.match(account_id=1, email_body="Preciso da segunda via do boleto", email_subject="Boleto")
    assert result is not None
    assert result["playbook_id"] == 1
    assert result["auto_respond"] is True


@pytest.mark.asyncio
async def test_match_no_company_profile():
    from orchestrator.services.playbook_service import PlaybookService
    db = AsyncMock()
    llm = AsyncMock()
    db.get_company_profile.return_value = None

    svc = PlaybookService(db, llm)
    result = await svc.match(account_id=1, email_body="Test", email_subject="Test")
    assert result is None


@pytest.mark.asyncio
async def test_match_no_playbooks():
    from orchestrator.services.playbook_service import PlaybookService
    db = AsyncMock()
    llm = AsyncMock()
    db.get_company_profile.return_value = {"id": 1, "company_name": "CW"}
    db.get_playbooks.return_value = []

    svc = PlaybookService(db, llm)
    result = await svc.match(account_id=1, email_body="Test", email_subject="Test")
    assert result is None


@pytest.mark.asyncio
async def test_generate_response():
    from orchestrator.services.playbook_service import PlaybookService
    db = AsyncMock()
    llm = AsyncMock()
    llm.generate_playbook_response.return_value = "Prezado João, segue segunda via do boleto."

    svc = PlaybookService(db, llm)
    company = {"company_name": "CodeWave", "tone": "formal", "signature": "Att, CodeWave"}
    template = "Prezado {nome_contato}, segue segunda via."
    result = await svc.generate_response(template, company, "João", "email body here")
    assert result is not None


@pytest.mark.asyncio
async def test_no_match_returns_none():
    from orchestrator.services.playbook_service import PlaybookService
    db = AsyncMock()
    llm = AsyncMock()
    db.get_company_profile.return_value = {"id": 1, "company_name": "CW"}
    db.get_playbooks.return_value = [
        {"id": 1, "trigger_description": "boleto", "response_template": "...", "auto_respond": True},
    ]
    llm.match_playbook.return_value = {"matched_id": None, "confidence": 0.1}

    svc = PlaybookService(db, llm)
    result = await svc.match(account_id=1, email_body="Hello", email_subject="Meeting")
    assert result is None
