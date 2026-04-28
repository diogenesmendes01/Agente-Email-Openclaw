"""Regression test: ação 'rascunho' não deve mais chamar gmail.create_draft."""
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_rascunho_action_does_not_call_create_draft():
    """Quando LLM decide acao='rascunho', NÃO devemos criar draft no Gmail."""
    from orchestrator.handlers.email_processor import EmailProcessor

    # Setup: mock todas as dependências
    gmail = MagicMock()
    gmail.create_draft = AsyncMock()  # se for chamado, capturamos
    gmail.archive_email = AsyncMock()

    db = MagicMock()
    db.get_account = AsyncMock(return_value={"id": 1})
    db.create_task = AsyncMock()

    processor = EmailProcessor(
        db=db,
        qdrant=MagicMock(),
        llm=MagicMock(),
        gmail=gmail,
        telegram=MagicMock(),
        learning=MagicMock(),
        pdf_reader=MagicMock(),
        metrics=MagicMock(),
    )

    action = {
        "acao": "rascunho",
        "rascunho_resposta": "Olá, vou responder amanhã.",
    }
    email = {"id": "abc", "from": "x@y.com", "subject": "Test", "threadId": "t1"}

    # Act
    await processor._execute_action(action, email, "conta_test")

    # Assert
    gmail.create_draft.assert_not_called()
