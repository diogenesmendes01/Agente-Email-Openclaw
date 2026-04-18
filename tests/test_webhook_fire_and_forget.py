"""Tests for fire-and-forget behavior of /telegram/callback endpoint."""
import asyncio
import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport


@pytest.mark.asyncio
async def test_callback_webhook_returns_200_immediately():
    """Webhook must return 200 in <200ms even if handler is slow."""
    from orchestrator.main import app

    async def slow_handler(*args, **kwargs):
        await asyncio.sleep(2.0)  # simulate slow Gmail/LLM

    mock_settings = MagicMock()
    mock_settings.telegram_allowed_user_ids = set()

    body = {
        "callback_query": {
            "id": "cb_test",
            "data": "archive:em_1:user@t.com",
            "from": {"id": 42},
            "message": {"message_id": 1, "chat": {"id": 1}, "text": "x"},
        }
    }

    with patch("orchestrator.main.handle_callback", slow_handler), \
         patch("orchestrator.main.get_settings", return_value=mock_settings), \
         patch.dict("os.environ", {"TELEGRAM_WEBHOOK_SECRET": ""}, clear=False):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            start = time.perf_counter()
            resp = await ac.post("/telegram/callback", json=body)
            elapsed = time.perf_counter() - start

    assert resp.status_code == 200
    assert elapsed < 0.5, f"Webhook blocked {elapsed:.2f}s (expected <0.5s)"
