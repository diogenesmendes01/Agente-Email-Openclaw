# tests/test_alert_service.py
import pytest
import time
from unittest.mock import AsyncMock, patch, MagicMock


class TestAlertService:

    @pytest.mark.asyncio
    async def test_sends_alert_dm(self):
        from orchestrator.services.alert_service import AlertService
        service = AlertService(
            bot_token="123:ABC",
            alert_user_id=999,
            throttle_minutes=15,
        )
        with patch("orchestrator.services.alert_service.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            sent = await service.alert("oauth_expired", "Token OAuth expirado para test@gmail.com")
            assert sent is True
            mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_throttles_duplicate_alerts(self):
        from orchestrator.services.alert_service import AlertService
        service = AlertService(
            bot_token="123:ABC",
            alert_user_id=999,
            throttle_minutes=15,
        )
        # Simulate recent alert
        service._last_sent["oauth_expired"] = time.monotonic()

        with patch("orchestrator.services.alert_service.httpx.AsyncClient") as MockClient:
            sent = await service.alert("oauth_expired", "Token OAuth expirado")
            assert sent is False
            MockClient.assert_not_called()

    @pytest.mark.asyncio
    async def test_different_alert_types_not_throttled(self):
        from orchestrator.services.alert_service import AlertService
        service = AlertService(
            bot_token="123:ABC",
            alert_user_id=999,
            throttle_minutes=15,
        )
        service._last_sent["oauth_expired"] = time.monotonic()

        with patch("orchestrator.services.alert_service.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            sent = await service.alert("service_failure", "Gmail API down 3x")
            assert sent is True

    @pytest.mark.asyncio
    async def test_throttle_not_consumed_on_send_failure(self):
        """When Telegram API returns non-200, the throttle should NOT be consumed."""
        from orchestrator.services.alert_service import AlertService
        service = AlertService(
            bot_token="123:ABC",
            alert_user_id=999,
            throttle_minutes=15,
        )

        with patch("orchestrator.services.alert_service.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 500
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            sent = await service.alert("service_failure", "test")
            assert sent is False
            # Throttle should NOT be consumed — can retry immediately
            assert "service_failure" not in service._last_sent
