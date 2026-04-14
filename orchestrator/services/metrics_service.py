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
        cost_usd: float = 0.0,
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
                        tokens_used, cost_usd, success, error_message)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
                    request_id, account_id, event, service,
                    latency_ms, tokens_used, cost_usd, success, error_message,
                )
        except Exception as e:
            logger.warning(f"Failed to record metric: {e}")

    async def get_cost_summary(self, account_id: int, days: int = 7) -> dict:
        """Get cost summary for the last N days.

        Returns dict with:
          - total_cost_usd, total_tokens, total_emails
          - daily breakdown [{date, cost_usd, tokens, emails}]
        """
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT
                         DATE(created_at) AS day,
                         SUM(cost_usd)::float AS cost_usd,
                         SUM(tokens_used) AS tokens,
                         COUNT(*) AS emails
                       FROM metrics
                       WHERE account_id = $1
                         AND event = 'email_processed'
                         AND success = true
                         AND created_at >= NOW() - $2 * INTERVAL '1 day'
                       GROUP BY DATE(created_at)
                       ORDER BY day DESC""",
                    account_id, days,
                )
                daily = [
                    {
                        "date": str(r["day"]),
                        "cost_usd": round(r["cost_usd"] or 0, 6),
                        "tokens": r["tokens"] or 0,
                        "emails": r["emails"],
                    }
                    for r in rows
                ]
                total_cost = sum(d["cost_usd"] for d in daily)
                total_tokens = sum(d["tokens"] for d in daily)
                total_emails = sum(d["emails"] for d in daily)
                return {
                    "days": days,
                    "total_cost_usd": round(total_cost, 6),
                    "total_tokens": total_tokens,
                    "total_emails": total_emails,
                    "daily": daily,
                }
        except Exception as e:
            logger.error(f"Failed to get cost summary: {e}")
            return {"days": days, "total_cost_usd": 0, "total_tokens": 0, "total_emails": 0, "daily": []}

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
