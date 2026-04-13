"""Alert service — sends DM alerts to operator via Telegram with throttling."""

import time
import logging
from typing import Dict

import httpx

logger = logging.getLogger(__name__)

ALERT_EMOJI = {
    "oauth_expired": "\u26a0\ufe0f",       # warning sign
    "service_failure": "\u274c",            # red X
    "queue_buildup": "\ud83d\udce8",        # envelope
    "watch_expiring": "\u23f0",             # alarm clock
    "job_dead": "\u2620\ufe0f",             # skull
}


class AlertService:
    """Sends DM alerts to operator's Telegram with per-type throttling."""

    def __init__(self, bot_token: str, alert_user_id: int, throttle_minutes: int = 15):
        self._bot_token = bot_token
        self._alert_user_id = alert_user_id
        self._throttle_seconds = throttle_minutes * 60
        self._last_sent: Dict[str, float] = {}

    async def alert(self, alert_type: str, message: str) -> bool:
        """Send an alert DM. Returns True if sent, False if throttled."""
        # Throttle check
        now = time.monotonic()
        last = self._last_sent.get(alert_type, 0)
        if now - last < self._throttle_seconds:
            logger.debug(f"Alert '{alert_type}' throttled (last sent {int(now - last)}s ago)")
            return False

        emoji = ALERT_EMOJI.get(alert_type, "\ud83d\udea8")  # default: rotating light
        text = f"{emoji} *Alert: {alert_type}*\n\n{message}"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"https://api.telegram.org/bot{self._bot_token}/sendMessage",
                    json={
                        "chat_id": self._alert_user_id,
                        "text": text,
                        "parse_mode": "Markdown",
                    },
                )
            self._last_sent[alert_type] = now
            if resp.status_code == 200:
                logger.info(f"Alert sent: {alert_type}")
                return True
            else:
                logger.warning(f"Alert send failed: {resp.status_code}")
                return False
        except Exception as e:
            logger.error(f"Alert send error: {e}")
            return False
