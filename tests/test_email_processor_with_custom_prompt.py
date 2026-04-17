"""Integration-ish: EmailProcessor must load account_prompt_config into the
LLM context so Layer 3 appears in generated prompts.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_account_prompt_config_reaches_llm_context():
    """When the account has a custom prompt config, EmailProcessor must
    pass it into the LLM context dict under 'account_prompt_config'.
    """
    from orchestrator.handlers.email_processor import EmailProcessor

    captured_contexts = []

    async def fake_classify(email, context, model_override=None):
        captured_contexts.append(("classify", dict(context)))
        return {"categoria": "outro", "prioridade": "Baixa", "importante": False,
                "confianca": 0.5, "razao": "test", "entidades": {}}, MagicMock(
                    model="m", retries=0, flags=[], json_parse_failed=False,
                    schema_valid=True, fallback_used=False,
                    prompt_tokens_successful=0, completion_tokens_successful=0,
                    prompt_tokens_total=0, completion_tokens_total=0,
                    cost_total_usd=0.0,
                )

    async def fake_summarize(email, classification, context=None, model_override=None):
        captured_contexts.append(("summary", dict(context or {})))
        return {"resumo": "r", "entidades": {}, "sentimento": "neutro"}, MagicMock(
            model="m", retries=0, flags=[], json_parse_failed=False,
            schema_valid=True, fallback_used=False,
            prompt_tokens_successful=0, completion_tokens_successful=0,
            prompt_tokens_total=0, completion_tokens_total=0,
            cost_total_usd=0.0,
        )

    async def fake_action(email, classification, summary, account_config, context=None, model_override=None):
        captured_contexts.append(("action", dict(context or {})))
        return {"acao": "notificar", "justificativa": "j", "acao_usuario": "u"}, MagicMock(
            model="m", retries=0, flags=[], json_parse_failed=False,
            schema_valid=True, fallback_used=False,
            prompt_tokens_successful=0, completion_tokens_successful=0,
            prompt_tokens_total=0, completion_tokens_total=0,
            cost_total_usd=0.0,
        )

    llm = AsyncMock()
    llm.classify_email.side_effect = fake_classify
    llm.summarize_email.side_effect = fake_summarize
    llm.decide_action.side_effect = fake_action
    llm.create_embedding.return_value = None

    db = AsyncMock()
    db.get_account_config.return_value = {"vips": [], "urgency_words": [], "ignore_words": [], "projetos": []}
    db.get_account.return_value = {"id": 42, "email": "u@x.com", "llm_model": None, "owner_name": ""}
    db.get_company_profile.return_value = None
    db.get_account_prompt_config.return_value = {"tom_adicional": "MUITO_INFORMAL_MARKER"}
    db.log_decision.return_value = 1
    db.log_llm_quality.return_value = None
    db.get_learning_counter.return_value = 0

    gmail = AsyncMock()
    gmail.get_email.return_value = {"from": "a@b.com", "subject": "s", "body": "body",
                                    "threadId": None, "attachments": []}

    qdrant = MagicMock()
    qdrant.is_connected.return_value = False

    tg = AsyncMock()
    tg.send_email_notification.return_value = 99

    proc = EmailProcessor(
        db=db, qdrant=qdrant, llm=llm, gmail=gmail, telegram=tg,
    )

    result = await proc.process_email(email_id="e1", account="u@x.com")
    assert result["status"] == "success"

    # All 3 LLM calls got a context containing account_prompt_config
    assert len(captured_contexts) == 3
    for _kind, ctx in captured_contexts:
        assert ctx.get("account_prompt_config") == {"tom_adicional": "MUITO_INFORMAL_MARKER"}


@pytest.mark.asyncio
async def test_no_config_means_no_key_in_context():
    """When account_prompt_config is None (or empty), the key should NOT
    be set in the context — preserving backward-compat behaviour.
    """
    from orchestrator.handlers.email_processor import EmailProcessor

    captured = []

    async def fake_classify(email, context, model_override=None):
        captured.append(dict(context))
        return {"categoria": "outro", "prioridade": "Baixa", "importante": False,
                "confianca": 0.5, "razao": "r", "entidades": {}}, MagicMock(
                    model="m", retries=0, flags=[], json_parse_failed=False,
                    schema_valid=True, fallback_used=False,
                    prompt_tokens_successful=0, completion_tokens_successful=0,
                    prompt_tokens_total=0, completion_tokens_total=0,
                    cost_total_usd=0.0,
                )

    async def fake_other(*a, **k):
        return {"resumo": "r", "acao": "notificar"}, MagicMock(
            model="m", retries=0, flags=[], json_parse_failed=False,
            schema_valid=True, fallback_used=False,
            prompt_tokens_successful=0, completion_tokens_successful=0,
            prompt_tokens_total=0, completion_tokens_total=0,
            cost_total_usd=0.0,
        )

    llm = AsyncMock()
    llm.classify_email.side_effect = fake_classify
    llm.summarize_email.side_effect = fake_other
    llm.decide_action.side_effect = fake_other
    llm.create_embedding.return_value = None

    db = AsyncMock()
    db.get_account_config.return_value = {"vips": [], "urgency_words": [], "ignore_words": [], "projetos": []}
    db.get_account.return_value = {"id": 42, "email": "u@x.com", "llm_model": None, "owner_name": ""}
    db.get_company_profile.return_value = None
    db.get_account_prompt_config.return_value = None   # <-- no custom config
    db.log_decision.return_value = 1
    db.log_llm_quality.return_value = None
    db.get_learning_counter.return_value = 0

    gmail = AsyncMock()
    gmail.get_email.return_value = {"from": "a@b.com", "subject": "s", "body": "body",
                                    "threadId": None, "attachments": []}

    qdrant = MagicMock()
    qdrant.is_connected.return_value = False

    tg = AsyncMock()
    tg.send_email_notification.return_value = 99

    proc = EmailProcessor(db=db, qdrant=qdrant, llm=llm, gmail=gmail, telegram=tg)
    result = await proc.process_email(email_id="e1", account="u@x.com")
    assert result["status"] == "success"
    assert "account_prompt_config" not in captured[0]
