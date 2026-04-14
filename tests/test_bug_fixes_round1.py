"""Tests for bug fixes round 1 — 7 issues + 4 test gaps."""
import json
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ═══════════════════════════════════════════════════════════════
# Fix #1 — callback_data account propagation
# ═══════════════════════════════════════════════════════════════


def test_create_keyboard_uses_explicit_account():
    """_create_keyboard should use the explicit account parameter, not action dict."""
    with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "-100"}):
        from orchestrator.services.telegram_service import TelegramService
        tg = TelegramService()
        email = {"id": "em1", "from_email": "sender@test.com", "from": "sender@test.com"}
        keyboard = tg._create_keyboard(email, "myaccount@gmail.com")
        # Every callback_data should contain the explicit account
        for row in keyboard["inline_keyboard"]:
            for btn in row:
                if "callback_data" in btn:
                    assert "myaccount@gmail.com" in btn["callback_data"], (
                        f"Button '{btn['text']}' missing account in callback_data"
                    )


def test_create_keyboard_no_gog_hook_fallback():
    """callback_data should never fall back to GOG_HOOK_ACCOUNT env var."""
    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "-100",
        "GOG_HOOK_ACCOUNT": "wrong@legacy.com",
    }):
        from orchestrator.services.telegram_service import TelegramService
        tg = TelegramService()
        email = {"id": "em1"}
        keyboard = tg._create_keyboard(email, "correct@gmail.com")
        for row in keyboard["inline_keyboard"]:
            for btn in row:
                if "callback_data" in btn:
                    assert "wrong@legacy.com" not in btn["callback_data"]


@pytest.mark.asyncio
async def test_pipeline_passes_account_to_notification():
    """EmailProcessor.process_email should pass account to send_email_notification."""
    from orchestrator.handlers.email_processor import EmailProcessor
    db = AsyncMock()
    qdrant = MagicMock()
    qdrant.is_connected.return_value = False
    llm = AsyncMock()
    gmail = AsyncMock()
    telegram = AsyncMock()

    proc = EmailProcessor(db, qdrant, llm, gmail, telegram)

    gmail.get_email.return_value = {
        "id": "em1", "from": "s@t.com", "from_email": "s@t.com",
        "from_name": "Sender", "subject": "Sub", "body": "Hello",
        "body_clean": "", "attachments": [], "threadId": "t1", "date": "2026-04-14",
    }
    db.get_account.return_value = {"id": 1}
    db.get_account_config.return_value = {"vips": [], "telegram_topic": 11}
    llm.classify_email.return_value = {"prioridade": "Média", "importante": True, "confianca": 0.8, "categoria": "outro"}
    llm.summarize_email.return_value = {"resumo": "Test"}
    llm.decide_action.return_value = {"acao": "notificar"}
    telegram.send_email_notification.return_value = 100
    db.log_decision.return_value = 1

    await proc.process_email("em1", "myaccount@gmail.com")
    call_kwargs = telegram.send_email_notification.call_args
    assert call_kwargs.kwargs.get("account") == "myaccount@gmail.com"


# ═══════════════════════════════════════════════════════════════
# Fix #2 — webhook error handling (retry on 500)
# ═══════════════════════════════════════════════════════════════


def test_webhook_error_handling_code_review():
    """Verify the telegram_callback endpoint has correct error handling structure.

    We verify the source code because the full app import requires DB/services.
    The endpoint should:
    - Catch (json.JSONDecodeError, ValueError) on parse phase only → 200 (no retry)
    - Catch generic Exception on processing phase → 500 (Telegram retries)
    """
    import inspect
    # We can't easily import the full app, so verify the source code pattern
    main_path = os.path.join(os.path.dirname(__file__), "..", "orchestrator", "main.py")
    with open(main_path) as f:
        source = f.read()

    # Should have the parse-error handler returning 200 (only for JSON parse, not KeyError)
    assert "json.JSONDecodeError, ValueError" in source, \
        "Missing parse-error catch for JSONDecodeError/ValueError"
    assert "KeyError" not in source.split("await request.json()")[1].split("bad_request")[0], \
        "KeyError should NOT be caught in the parse phase — it indicates a server bug"
    assert 'status_code=200, content={"status": "bad_request"}' in source, \
        "Parse errors should return 200 with bad_request"

    # Should have the generic-error handler returning 500
    assert 'status_code=500, content={"status": "error"}' in source, \
        "Transient errors should return 500 for Telegram retry"


# ═══════════════════════════════════════════════════════════════
# Fix #3 — playbook confidence threshold
# ═══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_playbook_low_confidence_rejected():
    """A playbook match with confidence below MIN_CONFIDENCE should return None."""
    from orchestrator.services.playbook_service import PlaybookService
    db = AsyncMock()
    llm = AsyncMock()
    db.get_company_profile.return_value = {"id": 1, "company_name": "CW"}
    db.get_playbooks.return_value = [
        {"id": 1, "trigger_description": "boleto", "response_template": "...", "auto_respond": True},
    ]
    llm.match_playbook.return_value = {"matched_id": 1, "confidence": 0.4}  # below 0.7

    svc = PlaybookService(db, llm)
    result = await svc.match(account_id=1, email_body="maybe boleto?", email_subject="Question")
    assert result is None


@pytest.mark.asyncio
async def test_playbook_high_confidence_accepted():
    """A playbook match with confidence >= MIN_CONFIDENCE should proceed."""
    from orchestrator.services.playbook_service import PlaybookService
    db = AsyncMock()
    llm = AsyncMock()
    db.get_company_profile.return_value = {"id": 1, "company_name": "CW", "tone": "formal", "signature": "Att"}
    db.get_playbooks.return_value = [
        {"id": 1, "trigger_description": "boleto", "response_template": "...", "auto_respond": True},
    ]
    llm.match_playbook.return_value = {"matched_id": 1, "confidence": 0.85}

    svc = PlaybookService(db, llm)
    result = await svc.match(account_id=1, email_body="Preciso da segunda via do boleto", email_subject="Boleto")
    assert result is not None
    assert result["playbook_id"] == 1
    assert result["confidence"] == 0.85


@pytest.mark.asyncio
async def test_playbook_boundary_confidence():
    """Confidence exactly at MIN_CONFIDENCE should be accepted."""
    from orchestrator.services.playbook_service import PlaybookService
    db = AsyncMock()
    llm = AsyncMock()
    db.get_company_profile.return_value = {"id": 1, "company_name": "CW", "tone": "formal", "signature": ""}
    db.get_playbooks.return_value = [
        {"id": 1, "trigger_description": "test", "response_template": "...", "auto_respond": False},
    ]
    llm.match_playbook.return_value = {"matched_id": 1, "confidence": 0.7}

    svc = PlaybookService(db, llm)
    result = await svc.match(account_id=1, email_body="test", email_subject="test")
    assert result is not None


# ═══════════════════════════════════════════════════════════════
# Fix #4 — company profile + domain rules in pipeline context
# ═══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_pipeline_fetches_company_profile_and_domain_rules():
    """process_email should include company profile and domain rules in LLM context."""
    from orchestrator.handlers.email_processor import EmailProcessor
    db = AsyncMock()
    qdrant = MagicMock()
    qdrant.is_connected.return_value = False
    llm = AsyncMock()
    gmail = AsyncMock()
    telegram = AsyncMock()

    proc = EmailProcessor(db, qdrant, llm, gmail, telegram)

    gmail.get_email.return_value = {
        "id": "em1", "from": "s@t.com", "from_email": "s@t.com",
        "from_name": "S", "subject": "Sub", "body": "Hello",
        "body_clean": "", "attachments": [], "threadId": "t1", "date": "2026-04-14",
    }
    db.get_account.return_value = {"id": 1}
    db.get_account_config.return_value = {"vips": [], "telegram_topic": 11}
    db.get_company_profile.return_value = {
        "id": 5, "company_name": "CodeWave", "cnpj": "12345",
        "tone": "empático", "signature": "Att, CW", "language": "pt-BR",
        "whatsapp_url": "",
    }
    db.get_domain_rules.return_value = [
        {"domain": "@important.com", "category": "cliente", "min_priority": "Alta", "default_action": "notificar"},
    ]
    llm.classify_email.return_value = {"prioridade": "Média", "importante": True, "confianca": 0.8, "categoria": "outro"}
    llm.summarize_email.return_value = {"resumo": "Test"}
    llm.decide_action.return_value = {"acao": "notificar"}
    telegram.send_email_notification.return_value = 100
    db.log_decision.return_value = 1

    await proc.process_email("em1", "u@t.com")

    # Check that classify_email received company_profile in context
    classify_call = llm.classify_email.call_args
    context = classify_call[0][1]
    assert "company_profile" in context
    assert context["company_profile"]["nome"] == "CodeWave"
    assert context["company_profile"]["tom"] == "empático"
    # Check domain rules
    assert "domain_rules" in context
    assert context["domain_rules"][0]["dominio"] == "@important.com"


# ═══════════════════════════════════════════════════════════════
# Fix #5 — body_clean in LLM prompts
# ═══════════════════════════════════════════════════════════════


def test_classifier_prompt_uses_body_clean():
    """_build_classifier_prompt should use body_clean when available."""
    from orchestrator.services.llm_service import LLMService
    llm = LLMService.__new__(LLMService)
    llm.MAX_PROMPT_TOKENS = 999999  # disable truncation
    email = {"from": "a@b.com", "to": "c@d.com", "subject": "Test",
             "body": "RAW BODY TEXT", "body_clean": "CLEANED BODY TEXT WITH PDF"}
    context = {"vips": [], "urgency_words": [], "ignore_words": []}
    prompt = llm._build_classifier_prompt(email, context)
    assert "CLEANED BODY TEXT WITH PDF" in prompt
    assert "RAW BODY TEXT" not in prompt


def test_classifier_prompt_falls_back_to_body():
    """When body_clean is empty, should fall back to body."""
    from orchestrator.services.llm_service import LLMService
    llm = LLMService.__new__(LLMService)
    llm.MAX_PROMPT_TOKENS = 999999
    email = {"from": "a@b.com", "to": "c@d.com", "subject": "Test",
             "body": "FALLBACK BODY", "body_clean": ""}
    context = {"vips": [], "urgency_words": [], "ignore_words": []}
    prompt = llm._build_classifier_prompt(email, context)
    assert "FALLBACK BODY" in prompt


def test_summarizer_prompt_uses_body_clean():
    """_build_summarizer_prompt should use body_clean when available."""
    from orchestrator.services.llm_service import LLMService
    llm = LLMService.__new__(LLMService)
    llm.MAX_PROMPT_TOKENS = 999999
    email = {"from": "a@b.com", "subject": "Test",
             "body": "RAW", "body_clean": "CLEANED TEXT"}
    classification = {"categoria": "outro", "prioridade": "Média"}
    prompt = llm._build_summarizer_prompt(email, classification)
    assert "CLEANED TEXT" in prompt
    assert "RAW" not in prompt


# ═══════════════════════════════════════════════════════════════
# Fix #6 — /config_identidade without account rejects early
# ═══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_config_identidade_no_account_rejects():
    """/config_identidade should reject when no account is found for the topic."""
    from orchestrator.handlers.telegram_commands import handle_command
    services = {"db": AsyncMock(), "telegram": AsyncMock(), "llm": AsyncMock(), "gmail": AsyncMock()}
    services["db"].get_account_by_topic.return_value = None  # no account
    msg = {"chat": {"id": 100}, "from": {"id": 42}, "text": "/config_identidade"}
    await handle_command(msg, services)
    # Should send error, NOT create a pending action
    services["db"].create_pending_action.assert_not_called()
    call_text = services["telegram"].send_text.call_args[0][1]
    assert "não encontrada" in call_text.lower() or "conta" in call_text.lower()


@pytest.mark.asyncio
async def test_config_playbook_no_account_rejects():
    """/config_playbook should reject when no account is found for the topic."""
    from orchestrator.handlers.telegram_commands import handle_command
    services = {"db": AsyncMock(), "telegram": AsyncMock(), "llm": AsyncMock(), "gmail": AsyncMock()}
    services["db"].get_account_by_topic.return_value = None
    msg = {"chat": {"id": 100}, "from": {"id": 42}, "text": "/config_playbook"}
    await handle_command(msg, services)
    services["db"].create_pending_action.assert_not_called()


# ═══════════════════════════════════════════════════════════════
# Fix #7 — auto-responded email: no send_draft button
# ═══════════════════════════════════════════════════════════════


def test_keyboard_auto_responded_omits_reply_buttons():
    """When auto_responded=True, send_draft and custom_reply buttons should be absent."""
    with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "-100"}):
        from orchestrator.services.telegram_service import TelegramService
        tg = TelegramService()
        email = {"id": "em1"}
        keyboard = tg._create_keyboard(email, "a@b.com", auto_responded=True)
        all_data = [btn.get("callback_data", "") for row in keyboard["inline_keyboard"] for btn in row]
        assert not any("send_draft" in d for d in all_data), "send_draft should be omitted"
        assert not any("custom_reply" in d for d in all_data), "custom_reply should be omitted"
        assert not any("silence" in d for d in all_data), "silence should be omitted"
        assert not any("spam" in d for d in all_data), "spam should be omitted"
        # Should still have archive, vip, create_task, reclassify
        assert any("archive" in d for d in all_data)
        assert any("create_task" in d for d in all_data)


def test_keyboard_normal_has_all_buttons():
    """When auto_responded=False (default), all buttons should be present."""
    with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "-100"}):
        from orchestrator.services.telegram_service import TelegramService
        tg = TelegramService()
        email = {"id": "em1"}
        keyboard = tg._create_keyboard(email, "a@b.com")
        all_data = [btn.get("callback_data", "") for row in keyboard["inline_keyboard"] for btn in row]
        assert any("send_draft" in d for d in all_data)
        assert any("custom_reply" in d for d in all_data)
        assert any("archive" in d for d in all_data)


@pytest.mark.asyncio
async def test_pipeline_auto_responded_marks_notification():
    """After auto-response, send_email_notification should receive auto_responded=True."""
    from orchestrator.handlers.email_processor import EmailProcessor
    db = AsyncMock()
    qdrant = MagicMock()
    qdrant.is_connected.return_value = False
    llm = AsyncMock()
    gmail = AsyncMock()
    telegram = AsyncMock()
    playbook_svc = AsyncMock()

    proc = EmailProcessor(db, qdrant, llm, gmail, telegram, playbook_service=playbook_svc)

    gmail.get_email.return_value = {
        "id": "em1", "from": "c@t.com", "from_email": "c@t.com",
        "from_name": "Client", "subject": "Boleto", "body": "preciso boleto",
        "body_clean": "", "attachments": [], "threadId": "t1", "date": "2026-04-14",
    }
    db.get_account.return_value = {"id": 1}
    db.get_account_config.return_value = {"vips": [], "telegram_topic": 11}
    llm.classify_email.return_value = {"prioridade": "Média", "importante": True, "confianca": 0.8, "categoria": "financeiro"}
    llm.summarize_email.return_value = {"resumo": "Boleto request"}
    llm.decide_action.return_value = {"acao": "notificar"}
    telegram.send_email_notification.return_value = 100
    db.log_decision.return_value = 1

    playbook_svc.match.return_value = {
        "playbook_id": 1, "template": "...", "trigger": "boleto",
        "auto_respond": True, "confidence": 0.9,
        "company": {"company_name": "CW", "tone": "formal", "signature": "Att"},
    }
    playbook_svc.generate_response.return_value = "Auto reply text"

    await proc.process_email("em1", "u@t.com")
    call_kwargs = telegram.send_email_notification.call_args
    assert call_kwargs.kwargs.get("auto_responded") is True


# ═══════════════════════════════════════════════════════════════
# Round 2 review — residual risk tests
# ═══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_send_reply_false_keeps_auto_responded_false():
    """When gmail.send_reply() returns False, auto_responded must stay False."""
    from orchestrator.handlers.email_processor import EmailProcessor
    db = AsyncMock()
    qdrant = MagicMock()
    qdrant.is_connected.return_value = False
    llm = AsyncMock()
    gmail = AsyncMock()
    telegram = AsyncMock()
    playbook_svc = AsyncMock()

    proc = EmailProcessor(db, qdrant, llm, gmail, telegram, playbook_service=playbook_svc)

    gmail.get_email.return_value = {
        "id": "em1", "from": "c@t.com", "from_email": "c@t.com",
        "from_name": "Client", "subject": "Boleto", "body": "preciso boleto",
        "body_clean": "", "attachments": [], "threadId": "t1", "date": "2026-04-14",
    }
    db.get_account.return_value = {"id": 1}
    db.get_account_config.return_value = {"vips": [], "telegram_topic": 11}
    llm.classify_email.return_value = {"prioridade": "Média", "importante": True, "confianca": 0.8, "categoria": "financeiro"}
    llm.summarize_email.return_value = {"resumo": "Boleto request"}
    llm.decide_action.return_value = {"acao": "notificar"}
    telegram.send_email_notification.return_value = 100
    db.log_decision.return_value = 1

    playbook_svc.match.return_value = {
        "playbook_id": 1, "template": "...", "trigger": "boleto",
        "auto_respond": True, "confidence": 0.9,
        "company": {"company_name": "CW", "tone": "formal", "signature": "Att"},
    }
    playbook_svc.generate_response.return_value = "Auto reply text"
    # Simulate send_reply returning False (send failed silently)
    gmail.send_reply.return_value = False

    await proc.process_email("em1", "u@t.com")
    call_kwargs = telegram.send_email_notification.call_args
    assert call_kwargs.kwargs.get("auto_responded") is False, \
        "auto_responded should be False when send_reply returns False"


def test_cancel_keyboard_respects_auto_responded():
    """After cancel, _build_original_keyboard must return reduced keyboard for auto-responded emails."""
    from orchestrator.handlers.telegram_callbacks import _build_original_keyboard

    # Normal email: all buttons present
    kb_normal = _build_original_keyboard("em1", "a@b.com", auto_responded=False)
    all_data_normal = [btn.get("callback_data", "") for row in kb_normal["inline_keyboard"] for btn in row]
    assert any("send_draft" in d for d in all_data_normal), "Normal keyboard should have send_draft"
    assert any("custom_reply" in d for d in all_data_normal), "Normal keyboard should have custom_reply"
    assert any("silence" in d for d in all_data_normal), "Normal keyboard should have silence"
    assert any("spam" in d for d in all_data_normal), "Normal keyboard should have spam"

    # Auto-responded email: reply buttons omitted
    kb_auto = _build_original_keyboard("em1", "a@b.com", auto_responded=True)
    all_data_auto = [btn.get("callback_data", "") for row in kb_auto["inline_keyboard"] for btn in row]
    assert not any("send_draft" in d for d in all_data_auto), "Auto-responded keyboard must omit send_draft"
    assert not any("custom_reply" in d for d in all_data_auto), "Auto-responded keyboard must omit custom_reply"
    assert not any("silence" in d for d in all_data_auto), "Auto-responded keyboard must omit silence"
    assert not any("spam" in d for d in all_data_auto), "Auto-responded keyboard must omit spam"
    # But should still have archive and reclassify
    assert any("archive" in d for d in all_data_auto), "Auto-responded keyboard should have archive"
    assert any("reclassify" in d for d in all_data_auto), "Auto-responded keyboard should have reclassify"


@pytest.mark.asyncio
async def test_cancel_flow_restores_reduced_keyboard_for_auto_responded():
    """cancel_ flow must detect auto-responded text and restore the reduced keyboard."""
    from orchestrator.handlers.telegram_callbacks import handle_callback

    db = AsyncMock()
    tg = AsyncMock()
    gmail = AsyncMock()
    llm = AsyncMock()

    original_text = "📧 Email\nDe: x@y.com\n\n✅ <b>Auto-respondido via playbook</b>"
    db.get_pending_action.return_value = {
        "id": 1, "message_id": 42,
        "state": json.dumps({"original_text": original_text, "account": "a@b.com", "sender": "x@y.com"}),
    }
    db.delete_pending_action.return_value = None

    callback_query = {
        "id": "cb1",
        "data": "cancel_archive:em1:a@b.com",
        "from": {"id": 123},
        "message": {
            "chat": {"id": -100},
            "message_thread_id": 11,
            "message_id": 42,
            "text": "Confirmar?",
        },
    }
    services = {"db": db, "gmail": gmail, "telegram": tg, "llm": llm, "allowed_user_ids": [123]}

    await handle_callback(callback_query, services)

    # Verify edit_message was called with a keyboard that omits reply buttons
    tg.edit_message.assert_called_once()
    call_kwargs = tg.edit_message.call_args
    reply_markup = call_kwargs.kwargs.get("reply_markup") or call_kwargs[1].get("reply_markup")
    all_data = [btn.get("callback_data", "") for row in reply_markup["inline_keyboard"] for btn in row]
    assert not any("send_draft" in d for d in all_data), "Reduced keyboard must omit send_draft after cancel"
    assert not any("custom_reply" in d for d in all_data), "Reduced keyboard must omit custom_reply after cancel"


@pytest.mark.asyncio
async def test_confidence_string_and_invalid_values():
    """Playbook confidence must handle string numbers and non-numeric strings."""
    from orchestrator.services.playbook_service import PlaybookService

    db = AsyncMock()
    llm = AsyncMock()
    svc = PlaybookService(db, llm)

    db.get_company_profile.return_value = {"id": 1, "company_name": "CW"}
    db.get_playbooks.return_value = [{"id": 10, "trigger_description": "boleto", "response_template": "t", "auto_respond": True}]

    # Case 1: confidence as string "0.85" — should be accepted (above 0.7)
    llm.match_playbook.return_value = {"matched_id": 10, "confidence": "0.85"}
    result = await svc.match(1, "body", "subject")
    assert result is not None, "String confidence '0.85' should be accepted as valid float"
    assert result["playbook_id"] == 10

    # Case 2: confidence as non-numeric string "high" — should fallback to 0.0 and be rejected
    llm.match_playbook.return_value = {"matched_id": 10, "confidence": "high"}
    result = await svc.match(1, "body", "subject")
    assert result is None, "Non-numeric confidence 'high' should fallback to 0.0 and be rejected"

    # Case 3: confidence as None — should fallback to 0.0 and be rejected
    llm.match_playbook.return_value = {"matched_id": 10, "confidence": None}
    result = await svc.match(1, "body", "subject")
    assert result is None, "None confidence should fallback to 0.0 and be rejected"


def test_webhook_parse_errors_isolated_from_processing():
    """Webhook must have two separate try/except blocks:
    1. Parse phase (JSONDecodeError, ValueError) -> 200 bad_request
    2. Processing phase (Exception) -> 500 error

    KeyError must NOT be caught in the parse phase — it indicates a server bug.
    """
    import re
    main_path = os.path.join(os.path.dirname(__file__), "..", "orchestrator", "main.py")
    with open(main_path) as f:
        source = f.read()

    # Extract the telegram_callback function body
    func_match = re.search(r'async def telegram_callback\(.*?\n(?=@app\.|\nif __name__|$)', source, re.DOTALL)
    assert func_match, "Could not find telegram_callback function"
    func_body = func_match.group(0)

    # Parse phase: should catch JSONDecodeError/ValueError right after request.json()
    # and return 200 before any handle_callback/handle_text_message
    parse_try = re.search(r'try:\s*\n\s*body = await request\.json\(\)', func_body)
    assert parse_try, "Parse phase should have 'try: body = await request.json()'"

    parse_except = re.search(r'except\s*\(json\.JSONDecodeError,\s*ValueError\)', func_body)
    assert parse_except, "Parse phase should catch (json.JSONDecodeError, ValueError) only"

    # Processing phase: separate try block wrapping handle_callback/handle_text_message
    # The parse except must come BEFORE handle_callback in the source
    parse_except_pos = parse_except.start()
    handle_pos = func_body.find("handle_callback")
    assert parse_except_pos < handle_pos, \
        "Parse error handler must be before handle_callback — parse and processing must be separate try blocks"

    # KeyError must not appear in the parse-phase except
    parse_section = func_body[:handle_pos]
    assert "KeyError" not in parse_section, \
        "KeyError must NOT be caught in parse phase — it indicates a server bug, not malformed input"

    # Processing phase should catch generic Exception -> 500
    processing_section = func_body[handle_pos:]
    assert 'status_code=500' in processing_section, \
        "Processing errors should return 500 for Telegram retry"
