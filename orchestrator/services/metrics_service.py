"""Metrics collection service — records events to PostgreSQL metrics table."""

import time
import logging
from contextlib import asynccontextmanager
from typing import Optional
from orchestrator.middleware.request_id import get_request_id

logger = logging.getLogger(__name__)


class _TrackingContext:
    """Mutable context for the track() context manager."""
    def __init__(self):
        self.tokens_used: int = 0
        self.extra: dict = {}


class MetricsService:
    """Records operational metrics to the metrics table."""

    def __init__(self, pool):
        self._pool = pool

    async def record(
        self,
        event: str,
        service: str = "",
        account_id: Optional[int] = None,
        latency_ms: int = 0,
        tokens_used: int = 0,
        success: bool = True,
        error_message: str = "",
    ):
        """Record a single metric event."""
        try:
            request_id = get_request_id()
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO metrics
                       (request_id, account_id, event, service, latency_ms,
                        tokens_used, success, error_message)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
                    request_id, account_id, event, service,
                    latency_ms, tokens_used, success, error_message,
                )
        except Exception as e:
            logger.warning(f"Failed to record metric: {e}")

    @asynccontextmanager
    async def track(self, event: str, service: str = "", account_id: Optional[int] = None):
        """Context manager that times an operation and records it."""
        ctx = _TrackingContext()
        start = time.monotonic()
        success = True
        error_msg = ""
        try:
            yield ctx
        except Exception as e:
            success = False
            error_msg = str(e)
            raise
        finally:
            latency_ms = max(1, int((time.monotonic() - start) * 1000))
            await self.record(
                event=event,
                service=service,
                account_id=account_id,
                latency_ms=latency_ms,
                tokens_used=ctx.tokens_used,
                success=success,
                error_message=error_msg,
            )

    async def cleanup(self, retention_days: int = 90) -> str:
        """Delete metrics older than retention_days."""
        async with self._pool.acquire() as conn:
            return await conn.execute(
                "DELETE FROM metrics WHERE created_at < NOW() - $1 * INTERVAL '1 day'",
                retention_days,
            )
