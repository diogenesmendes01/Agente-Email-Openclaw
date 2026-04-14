# tests/test_metrics_service.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestMetricsService:

    @pytest.mark.asyncio
    async def test_record_metric(self, mock_pool):
        pool, conn = mock_pool
        conn.execute.return_value = None
        from orchestrator.services.metrics_service import MetricsService
        ms = MetricsService(pool)
        await ms.record(
            event="email_processed",
            service="pipeline",
            account_id=1,
            latency_ms=350,
            tokens_used=150,
            success=True,
        )
        conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_track_context_manager(self, mock_pool):
        pool, conn = mock_pool
        conn.execute.return_value = None
        from orchestrator.services.metrics_service import MetricsService
        ms = MetricsService(pool)
        async with ms.track("llm_call", service="llm", account_id=1) as t:
            t.tokens_used = 200
        conn.execute.assert_called_once()
        # Args: (sql, request_id, account_id, event, service, latency_ms, tokens_used, cost_usd, success, error_message)
        args = conn.execute.call_args.args
        assert args[5] > 0       # latency_ms > 0
        assert args[6] == 200    # tokens_used
        assert args[8] is True   # success

    @pytest.mark.asyncio
    async def test_track_records_failure(self, mock_pool):
        pool, conn = mock_pool
        conn.execute.return_value = None
        from orchestrator.services.metrics_service import MetricsService
        ms = MetricsService(pool)
        try:
            async with ms.track("llm_call", service="llm") as t:
                raise ValueError("test error")
        except ValueError:
            pass
        conn.execute.assert_called_once()
        # Args: (sql, request_id, account_id, event, service, latency_ms, tokens_used, cost_usd, success, error_message)
        args = conn.execute.call_args.args
        assert args[8] is False           # success = False
        assert "test error" in args[9]    # error_message

    @pytest.mark.asyncio
    async def test_cleanup_old_metrics(self, mock_pool):
        pool, conn = mock_pool
        conn.execute.return_value = "DELETE 42"
        from orchestrator.services.metrics_service import MetricsService
        ms = MetricsService(pool)
        result = await ms.cleanup(retention_days=90)
        conn.execute.assert_called_once()
        assert result is not None
