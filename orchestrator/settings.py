"""Unified settings loaded from .env and validated at startup."""

import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_REQUIRED = [
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_ALLOWED_USER_IDS",
    "TELEGRAM_WEBHOOK_SECRET",
    "TELEGRAM_ALERT_USER_ID",
    "DATABASE_URL",
    "FUNNEL_BASE_URL",
]


class Settings:
    """Loads and validates all configuration from environment variables."""

    def __init__(self):
        # Validate required vars
        missing = [v for v in _REQUIRED if not os.getenv(v, "").strip()]
        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing)}"
            )

        # LLM
        self.openrouter_api_key: str = os.environ["OPENROUTER_API_KEY"]
        self.openai_api_key: str = os.environ["OPENAI_API_KEY"]
        self.llm_model: str = os.getenv("LLM_MODEL", "z-ai/glm-5-turbo")
        self.llm_vision_model: str = os.getenv("LLM_VISION_MODEL", "google/gemini-2.5-flash")
        self.embedding_model: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

        # Telegram
        self.telegram_bot_token: str = os.environ["TELEGRAM_BOT_TOKEN"]
        self.telegram_chat_id: str = os.environ["TELEGRAM_CHAT_ID"]
        self.telegram_webhook_secret: str = os.environ["TELEGRAM_WEBHOOK_SECRET"]
        self.telegram_alert_user_id: int = int(os.environ["TELEGRAM_ALERT_USER_ID"])
        self.telegram_allowed_user_ids: set = {
            int(uid.strip())
            for uid in os.environ["TELEGRAM_ALLOWED_USER_IDS"].split(",")
            if uid.strip()
        }

        # Database
        self.database_url: str = os.environ["DATABASE_URL"]

        # Base dir
        self.base_dir: str = os.getenv("EMAIL_AGENT_BASE_DIR", ".")

        # Qdrant
        self.qdrant_host: str = os.getenv("QDRANT_HOST", "localhost")
        self.qdrant_port: int = int(os.getenv("QDRANT_PORT", "6333"))

        # Tailscale
        self.funnel_base_url: str = os.environ["FUNNEL_BASE_URL"]

        # Gmail accounts: GMAIL_ACCOUNT_N → GMAIL_HOOK_TOKEN_N
        self.gmail_accounts: dict = {}
        for i in range(1, 20):
            account = os.getenv(f"GMAIL_ACCOUNT_{i}", "").strip()
            token = os.getenv(f"GMAIL_HOOK_TOKEN_{i}", "").strip()
            if account and token:
                self.gmail_accounts[account] = token
            else:
                break

        # Learning
        self.learning_interval: int = int(os.getenv("LEARNING_INTERVAL", "50"))

        logger.info(
            "Settings loaded: %d Gmail accounts, model=%s",
            len(self.gmail_accounts),
            self.llm_model,
        )


# Module-level singleton — import as: from orchestrator.settings import settings
settings: Settings = None  # type: ignore


def get_settings() -> Settings:
    """Lazy singleton — created on first call so tests can patch env first."""
    global settings
    if settings is None:
        settings = Settings()
    return settings
