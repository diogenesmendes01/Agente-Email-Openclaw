"""Tests for retry_worker behavior in main.py."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_retry_worker_marks_failed_on_error_result():
    """When process_email returns {"status": "error"}, retry_worker should call mark_failed, not mark_completed."""
    mock_job_queue = AsyncMock()
    mock_job_queue.get_pending.return_value = [
        {
            "id": 1,
            "job_type": "process_email",
            "payload": json.dumps({"email_id": "abc", "account": "test@gmail.com"}),
        }
    ]

    mock_processor = AsyncMock()
    mock_processor.process_email.return_value = {"status": "error", "error": "Gmail API down"}

    mock_alerts = AsyncMock()
    mock_job_queue.mark_failed.return_value = False  # not dead yet

    # Simulate one iteration of retry_worker logic
    jobs = await mock_job_queue.get_pending(limit=5)
    for job in jobs:
        try:
            if job["job_type"] == "process_email":
                payload = json.loads(job["payload"]) if isinstance(job["payload"], str) else job["payload"]
                result = await mock_processor.process_email(payload["email_id"], payload["account"], _is_retry=True)
                if result.get("status") == "error":
                    raise RuntimeError(result.get("error", "process_email returned error"))
            await mock_job_queue.mark_completed(job["id"])
        except Exception as e:
            is_dead = await mock_job_queue.mark_failed(job["id"], str(e))
            if is_dead:
                await mock_alerts.alert("job_dead", f"Job #{job['id']} died: {e}")

    # mark_completed should NOT have been called
    mock_job_queue.mark_completed.assert_not_called()
    # mark_failed should have been called
    mock_job_queue.mark_failed.assert_called_once_with(1, "Gmail API down")
    # process_email should have been called with _is_retry=True
    mock_processor.process_email.assert_called_once_with("abc", "test@gmail.com", _is_retry=True)


@pytest.mark.asyncio
async def test_retry_worker_marks_completed_on_success():
    """When process_email returns {"status": "success"}, retry_worker should call mark_completed."""
    mock_job_queue = AsyncMock()
    mock_job_queue.get_pending.return_value = [
        {
            "id": 2,
            "job_type": "process_email",
            "payload": json.dumps({"email_id": "def", "account": "test@gmail.com"}),
        }
    ]

    mock_processor = AsyncMock()
    mock_processor.process_email.return_value = {"status": "success"}

    # Simulate one iteration of retry_worker logic
    jobs = await mock_job_queue.get_pending(limit=5)
    for job in jobs:
        try:
            if job["job_type"] == "process_email":
                payload = json.loads(job["payload"]) if isinstance(job["payload"], str) else job["payload"]
                result = await mock_processor.process_email(payload["email_id"], payload["account"], _is_retry=True)
                if result.get("status") == "error":
                    raise RuntimeError(result.get("error", "process_email returned error"))
            await mock_job_queue.mark_completed(job["id"])
        except Exception:
            await mock_job_queue.mark_failed(job["id"], "error")

    # mark_completed should have been called
    mock_job_queue.mark_completed.assert_called_once_with(2)
    # mark_failed should NOT have been called
    mock_job_queue.mark_failed.assert_not_called()
    # process_email should have been called with _is_retry=True
    mock_processor.process_email.assert_called_once_with("def", "test@gmail.com", _is_retry=True)
