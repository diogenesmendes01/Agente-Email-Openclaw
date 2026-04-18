"""Tests for thread-awareness in EmailProcessor: detecting when the owner
has already replied and passing the flag through to the LLM context."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from orchestrator.services.llm_validator import ValidationMetadata


def _meta(kind: str) -> ValidationMetadata:
    return ValidationMetadata(kind=kind)


def _extract_context(call) -> dict:
    """Pick the dict arg that looks like the email-processing context.

    Robust to positional shuffling between classify_email(email, context),
    summarize_email(email, classification, context) and
    decide_action(email, classification, summary, config, context).
    The context dict always contains ``owner_email`` — other dict args
    (classification / summary / config) never do.
    """
    if "context" in call.kwargs:
        return call.kwargs["context"]
    for a in reversed(call.args):
        if isinstance(a, dict) and "owner_email" in a:
            return a
    raise AssertionError(f"No context dict found in call: args={call.args} kwargs={call.kwargs}")


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
    playbook_svc.match.return_value = None

    proc = EmailProcessor(db, qdrant, llm, gmail, telegram, playbook_service=playbook_svc)

    # Stable defaults used by every test
    db.get_account.return_value = {"id": 1, "owner_name": "Diogenes"}
    db.get_account_config.return_value = {"vips": [], "telegram_topic": 11}
    db.log_decision.return_value = 1
    llm.classify_email.return_value = (
        {"prioridade": "Baixa", "importante": True, "confianca": 0.7, "categoria": "outro"},
        _meta("classification"),
    )
    llm.summarize_email.return_value = ({"resumo": "resumo"}, _meta("summary"))
    llm.decide_action.return_value = ({"acao": "notificar"}, _meta("action"))
    telegram.send_email_notification.return_value = 100
    return proc


def _email(email_id="em1", thread_id="t1"):
    return {
        "id": email_id,
        "from": "Other <other@domain.com>",
        "from_email": "other@domain.com",
        "from_name": "Other",
        "subject": "Re: Parceria",
        "body": "Aguardando retorno",
        "body_clean": "",
        "attachments": [],
        "threadId": thread_id,
        "date": "2026-04-17",
        "labels": ["INBOX", "UNREAD"],
    }


@pytest.mark.asyncio
async def test_owner_already_replied_flag_true_when_last_msg_is_from_owner(processor):
    """Last message in thread from the owner -> flag should be True in context."""
    account = "me@domain.com"
    processor.gmail.get_email.return_value = _email()
    processor.gmail.get_thread.return_value = [
        {"from": "Other <other@domain.com>", "from_email": "other@domain.com", "body": "Ola", "date": "d1"},
        {"from": "Diogenes <me@domain.com>", "from_email": "me@domain.com", "body": "Obrigado", "date": "d2"},
    ]

    await processor.process_email("em1", account)

    # Classifier, summarizer and action all receive the same context flag
    for mock_call in [
        processor.llm.classify_email,
        processor.llm.summarize_email,
        processor.llm.decide_action,
    ]:
        mock_call.assert_called_once()
        ctx = _extract_context(mock_call.call_args)
        assert ctx.get("owner_already_replied") is True, (
            f"owner_already_replied should be True for {mock_call._mock_name or 'unknown'}"
        )


@pytest.mark.asyncio
async def test_flag_false_when_no_owner_msg_in_thread(processor):
    """Thread with only external messages -> flag False (owner never replied)."""
    account = "me@domain.com"
    processor.gmail.get_email.return_value = _email()
    processor.gmail.get_thread.return_value = [
        {"id": "pre1", "from": "Other <other@x.com>", "from_email": "other@x.com", "body": "Hi", "date": "d1"},
        {"id": "em1", "from": "Other <other@x.com>", "from_email": "other@x.com", "body": "Re", "date": "d2"},
    ]

    await processor.process_email("em1", account)

    ctx = _extract_context(processor.llm.classify_email.call_args)
    assert ctx.get("owner_already_replied") is False


@pytest.mark.asyncio
async def test_owner_detection_does_not_false_positive_on_substring(processor):
    """admin@x.com should NOT match admin@xavier.com — regression test."""
    account = "admin@x.com"
    processor.gmail.get_email.return_value = _email()
    processor.gmail.get_thread.return_value = [
        {"from": "Stranger <admin@xavier.com>", "from_email": "admin@xavier.com", "body": "hi", "date": "d1"},
    ]

    await processor.process_email("em1", account)

    ctx = _extract_context(processor.llm.classify_email.call_args)
    assert ctx.get("owner_already_replied") is False


@pytest.mark.asyncio
async def test_no_thread_context_flag_false(processor):
    """When threadId == email_id (single message thread), flag is False."""
    account = "me@domain.com"
    email = _email(email_id="em1", thread_id="em1")  # same id, single msg
    processor.gmail.get_email.return_value = email

    await processor.process_email("em1", account)

    # get_thread should NOT have been called
    processor.gmail.get_thread.assert_not_called()

    ctx = _extract_context(processor.llm.classify_email.call_args)
    assert ctx.get("owner_already_replied") is False


@pytest.mark.asyncio
async def test_flag_true_when_owner_replied_earlier_but_external_is_last(processor):
    """Regression (PR review item #1): common real-world sequence
        1. cliente sends
        2. dono replies
        3. cliente sends new msg
    The CURRENT email is msg 3 (external). The flag must still be True
    because the dono has already replied earlier in the thread.
    """
    account = "me@domain.com"
    processor.gmail.get_email.return_value = _email(email_id="em3")
    processor.gmail.get_thread.return_value = [
        {"id": "em1", "from": "Client <other@x.com>", "from_email": "other@x.com", "body": "Ola", "date": "d1"},
        {"id": "em2", "from": "Me <me@domain.com>", "from_email": "me@domain.com", "body": "Retornando", "date": "d2"},
        {"id": "em3", "from": "Client <other@x.com>", "from_email": "other@x.com", "body": "Duvida nova", "date": "d3"},
    ]

    await processor.process_email("em3", account)

    ctx = _extract_context(processor.llm.classify_email.call_args)
    assert ctx.get("owner_already_replied") is True, (
        "Must detect that the owner already replied (msg em2) even when "
        "the last message is from the external sender (msg em3)."
    )


@pytest.mark.asyncio
async def test_flag_ignores_current_email_when_it_is_owners(processor):
    """If only the CURRENT email is from the owner (no earlier reply),
    the flag should be False — we don't count the email being processed
    as a prior reply.
    """
    account = "me@domain.com"
    processor.gmail.get_email.return_value = _email(email_id="em1")
    processor.gmail.get_thread.return_value = [
        {"id": "em1", "from": "Me <me@domain.com>", "from_email": "me@domain.com", "body": "x", "date": "d1"},
    ]

    await processor.process_email("em1", account)

    ctx = _extract_context(processor.llm.classify_email.call_args)
    assert ctx.get("owner_already_replied") is False
