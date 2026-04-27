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

    @pytest.mark.asyncio
    async def test_handle_failure_fatal_marks_permanently(self, mock_pool):
        """FatalError should call mark_failed_permanently, not increment attempts."""
        from orchestrator.services.job_queue import JobQueue
        from orchestrator.errors import FatalError

        pool, conn = mock_pool
        conn.execute.return_value = None
        jq = JobQueue(pool)
        is_dead = await jq.handle_failure(job_id=1, exc=FatalError("bad json"))
        assert is_dead is True

        # Verify a single permanent-failed UPDATE was issued (status='dead' no
        # attempts increment, no fetchrow check). Existing retry path always
        # issues an attempts-increment UPDATE first; we should NOT see that.
        sql_calls = [str(call.args[0]).lower() for call in conn.execute.call_args_list]
        assert len(sql_calls) == 1
        sql = sql_calls[0]
        assert "status = 'dead'" in sql
        assert "attempts = attempts + 1" not in sql
        # And fetchrow (used by retry path to check max_attempts) must NOT run
        conn.fetchrow.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_failure_retryable_uses_existing_path(self, mock_pool):
        """RetryableError should go through the existing mark_failed (retry counter)."""
        from orchestrator.services.job_queue import JobQueue
        from orchestrator.errors import RetryableError

        pool, conn = mock_pool
        conn.execute.return_value = None
        # mark_failed checks attempts/max_attempts to decide if dead
        conn.fetchrow.return_value = {"attempts": 1, "max_attempts": 5}
        jq = JobQueue(pool)

        is_dead = await jq.handle_failure(job_id=1, exc=RetryableError("timeout"))
        assert is_dead is False

        # Verify attempts increment was issued (retry path)
        sql_calls = [str(call.args[0]).lower() for call in conn.execute.call_args_list]
        retry_calls = [s for s in sql_calls if "attempts = attempts + 1" in s]
        assert len(retry_calls) == 1
        # And fetchrow is consulted for max_attempts
        conn.fetchrow.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_failure_unknown_exception_defaults_fatal(self, mock_pool):
        """Unknown stdlib exceptions classify as Fatal -> permanent failure."""
        from orchestrator.services.job_queue import JobQueue

        pool, conn = mock_pool
        conn.execute.return_value = None
        jq = JobQueue(pool)

        # KeyError is classified as FatalError by classify_exception
        is_dead = await jq.handle_failure(job_id=42, exc=KeyError("missing"))
        assert is_dead is True
        # Did NOT increment attempts
        sql_calls = [str(call.args[0]).lower() for call in conn.execute.call_args_list]
        assert all("attempts = attempts + 1" not in s for s in sql_calls)
