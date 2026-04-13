# tests/test_job_queue.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestJobQueue:

    @pytest.mark.asyncio
    async def test_enqueue_job(self, mock_pool):
        pool, conn = mock_pool
        conn.fetchval.return_value = 1
        from orchestrator.services.job_queue import JobQueue
        jq = JobQueue(pool)
        job_id = await jq.enqueue(
            job_type="process_email",
            payload={"email_id": "abc", "account": "test@gmail.com"},
            account_id=1,
        )
        assert job_id == 1
        conn.fetchval.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_pending_jobs(self, mock_pool):
        pool, conn = mock_pool
        conn.fetch.return_value = [
            {"id": 1, "job_type": "process_email", "payload": '{"email_id": "abc"}',
             "attempts": 0, "max_attempts": 5, "account_id": 1},
        ]
        from orchestrator.services.job_queue import JobQueue
        jq = JobQueue(pool)
        jobs = await jq.get_pending(limit=10)
        assert len(jobs) == 1
        assert jobs[0]["id"] == 1

    @pytest.mark.asyncio
    async def test_mark_completed(self, mock_pool):
        pool, conn = mock_pool
        conn.execute.return_value = None
        from orchestrator.services.job_queue import JobQueue
        jq = JobQueue(pool)
        await jq.mark_completed(1)
        conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_mark_failed_becomes_dead(self, mock_pool):
        pool, conn = mock_pool
        conn.execute.return_value = None
        conn.fetchrow.return_value = {"attempts": 5, "max_attempts": 5}
        from orchestrator.services.job_queue import JobQueue
        jq = JobQueue(pool)
        is_dead = await jq.mark_failed(1, "Connection timeout")
        assert is_dead is True
        # Verify 2 execute calls: 1) update attempts, 2) set status='dead'
        assert conn.execute.call_count == 2
        dead_call = conn.execute.call_args_list[-1]
        assert "dead" in dead_call.args[0]

    @pytest.mark.asyncio
    async def test_mark_failed_not_dead_yet(self, mock_pool):
        pool, conn = mock_pool
        conn.execute.return_value = None
        conn.fetchrow.return_value = {"attempts": 2, "max_attempts": 5}
        from orchestrator.services.job_queue import JobQueue
        jq = JobQueue(pool)
        is_dead = await jq.mark_failed(1, "Timeout")
        assert is_dead is False
