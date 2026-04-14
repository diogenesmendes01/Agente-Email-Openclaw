"""Failed jobs queue with retry and exponential backoff."""

import json
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class JobQueue:
    """Manages failed jobs in PostgreSQL with retry logic."""

    def __init__(self, pool, max_attempts: int = 5):
        self._pool = pool
        self._max_attempts = max_attempts

    async def enqueue(
        self,
        job_type: str,
        payload: Dict[str, Any],
        account_id: Optional[int] = None,
        max_attempts: Optional[int] = None,
    ) -> int:
        """Add a job to the retry queue."""
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                """INSERT INTO failed_jobs
                   (account_id, job_type, payload, max_attempts, next_retry_at, status)
                   VALUES ($1, $2, $3::jsonb, $4, NOW(), 'pending')
                   RETURNING id""",
                account_id,
                job_type,
                json.dumps(payload),
                max_attempts or self._max_attempts,
            )

    async def get_pending(self, limit: int = 10) -> List[Dict]:
        """Get pending jobs that are ready for retry.

        Uses FOR UPDATE SKIP LOCKED so multiple workers never pick
        the same job, and atomically sets status to 'processing'.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """SELECT * FROM failed_jobs
                       WHERE status = 'pending' AND next_retry_at <= NOW()
                       ORDER BY next_retry_at ASC
                       LIMIT $1
                       FOR UPDATE SKIP LOCKED""",
                    limit,
                )
                if rows:
                    ids = [r["id"] for r in rows]
                    # Set next_retry_at = NOW() so the reaper measures stuck time
                    # from when the job was claimed, not from when it was enqueued.
                    await conn.execute(
                        """UPDATE failed_jobs
                           SET status = 'processing', next_retry_at = NOW()
                           WHERE id = ANY($1::int[])""",
                        ids,
                    )
                return [dict(r) for r in rows]

    async def mark_completed(self, job_id: int):
        """Mark a job as successfully completed."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE failed_jobs SET status = 'completed' WHERE id = $1",
                job_id,
            )

    async def mark_failed(self, job_id: int, error: str) -> bool:
        """Record a failure and return to 'pending' for next retry.
        Returns True if job is now dead (max attempts reached)."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """UPDATE failed_jobs
                   SET attempts = attempts + 1,
                       last_error = $2,
                       status = 'pending',
                       next_retry_at = NOW() + (POWER(2, attempts + 1) || ' minutes')::INTERVAL
                   WHERE id = $1""",
                job_id, error,
            )
            row = await conn.fetchrow(
                "SELECT attempts, max_attempts FROM failed_jobs WHERE id = $1",
                job_id,
            )
            if row and row["attempts"] >= row["max_attempts"]:
                await conn.execute(
                    "UPDATE failed_jobs SET status = 'dead' WHERE id = $1",
                    job_id,
                )
                return True
            return False

    async def get_dead_count(self) -> int:
        """Get count of dead jobs (for alerting)."""
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM failed_jobs WHERE status = 'dead'"
            ) or 0

    async def get_pending_count(self) -> int:
        """Get count of pending jobs (for alerting)."""
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM failed_jobs WHERE status = 'pending'"
            ) or 0

    async def reap_stuck_processing(self, timeout_minutes: int = 15) -> int:
        """Reset jobs stuck in 'processing' for longer than timeout back to 'pending'.

        This handles the case where a worker crashes after claiming a job
        but before calling mark_completed/mark_failed.
        Returns the number of jobs reaped.
        """
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """UPDATE failed_jobs
                   SET status = 'pending',
                       next_retry_at = NOW()
                   WHERE status = 'processing'
                     AND next_retry_at < NOW() - ($1 || ' minutes')::INTERVAL""",
                str(timeout_minutes),
            )
            count = int(result.split()[-1])
            if count > 0:
                logger.warning(f"Reaped {count} stuck processing jobs (timeout={timeout_minutes}m)")
            return count
