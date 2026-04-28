"""Integration: emails de senders no-reply nunca geram acao=rascunho."""
import pytest


@pytest.mark.asyncio
async def test_no_reply_sender_demotes_rascunho_to_notificar(monkeypatch):
    from orchestrator.services.llm_validator import demote_rascunho_if_non_replyable

    # Caso 1: sender no-reply + LLM tentou rascunho
    action = {"acao": "rascunho", "rascunho_resposta": "Resposta inutil"}
    result = demote_rascunho_if_non_replyable(
        action,
        from_addr="noreply@github.com",
        categoria="trabalho",
    )
    assert result["acao"] == "notificar"
    assert result.get("rascunho_resposta") is None
    assert result.get("flags", {}).get("rascunho_em_no_reply") is True

    # Caso 2: categoria newsletter
    action2 = {"acao": "rascunho", "rascunho_resposta": "Resposta a newsletter"}
    result2 = demote_rascunho_if_non_replyable(
        action2,
        from_addr="alguem@empresa.com",
        categoria="newsletter",
    )
    assert result2["acao"] == "notificar"

    # Caso 3: caso normal — não rebaixa
    action3 = {"acao": "rascunho", "rascunho_resposta": "Boa resposta"}
    result3 = demote_rascunho_if_non_replyable(
        action3,
        from_addr="cliente@empresa.com",
        categoria="cliente",
    )
    assert result3["acao"] == "rascunho"
    assert result3.get("rascunho_resposta") == "Boa resposta"
