"""Verify process_email re-raises the original exception when _is_retry=True.

Issue 2 do PR #17: o except top-level engolia a excecao original e retornava
``{status: "error"}``. _do_retry entao convertia em ``RuntimeError``, e
``classify_exception`` caia no default conservador ``FatalError`` -> erros
transitorios morriam na 1a tentativa.

Fix: quando ``_is_retry=True``, ``process_email`` deve relancar a excecao
original para que ``job_queue.handle_failure()`` consiga rotear corretamente
``Retryable`` vs ``Fatal``.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_process_email_reraises_typed_exception_on_retry():
    """When _is_retry=True, process_email must re-raise the original typed exception
    so the retry worker can route Retryable vs Fatal via handle_failure.
    """
    from orchestrator.handlers.email_processor import EmailProcessor
    from orchestrator.errors import RetryableError

    # Mock gmail to raise a RetryableError during fetch — the very first thing
    gmail = MagicMock()
    gmail.get_email = AsyncMock(side_effect=RetryableError("transient network"))

    processor = EmailProcessor(
        db=MagicMock(),
        qdrant=MagicMock(),
        llm=MagicMock(),
        gmail=gmail,
        telegram=MagicMock(),
    )

    # When called from webhook path (_is_retry=False), it should swallow and return dict
    result = await processor.process_email("abc", "conta_test", _is_retry=False)
    assert result["status"] == "error"

    # When called from retry path (_is_retry=True), it must re-raise the ORIGINAL
    # exception type so the queue can classify it.
    with pytest.raises(RetryableError, match="transient network"):
        await processor.process_email("abc", "conta_test", _is_retry=True)


@pytest.mark.asyncio
async def test_process_email_does_not_enqueue_retry_when_is_retry_true():
    """When called from the retry worker (_is_retry=True), the failing path must
    NOT enqueue another retry job. The retry worker is already handling the job —
    enqueueing a duplicate would compound failures.
    """
    from orchestrator.handlers.email_processor import EmailProcessor
    from orchestrator.errors import RetryableError

    gmail = MagicMock()
    gmail.get_email = AsyncMock(side_effect=RetryableError("transient"))

    job_queue = MagicMock()
    job_queue.enqueue = AsyncMock()

    db = MagicMock()
    db.get_account = AsyncMock(return_value={"id": 1})

    processor = EmailProcessor(
        db=db,
        qdrant=MagicMock(),
        llm=MagicMock(),
        gmail=gmail,
        telegram=MagicMock(),
        job_queue=job_queue,
    )

    # _is_retry=True must NOT enqueue another retry job
    with pytest.raises(RetryableError):
        await processor.process_email("abc", "conta_test", _is_retry=True)

    job_queue.enqueue.assert_not_called()
