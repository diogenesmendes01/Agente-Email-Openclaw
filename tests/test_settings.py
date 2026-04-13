import os
import pytest
from unittest.mock import patch


def _minimal_env():
    """Minimal valid env vars for Settings."""
    return {
        "OPENROUTER_API_KEY": "sk-or-test",
        "OPENAI_API_KEY": "sk-test",
        "TELEGRAM_BOT_TOKEN": "123:ABC",
        "TELEGRAM_CHAT_ID": "-100123",
        "TELEGRAM_ALLOWED_USER_IDS": "111,222",
        "TELEGRAM_WEBHOOK_SECRET": "secret",
        "TELEGRAM_ALERT_USER_ID": "111",
        "DATABASE_URL": "postgresql://user:pass@localhost:5432/test",
        "FUNNEL_BASE_URL": "https://machine.ts.net",
        "GMAIL_ACCOUNT_1": "test@gmail.com",
        "GMAIL_HOOK_TOKEN_1": "token123",
    }


class TestSettings:
    def test_loads_valid_env(self):
        with patch.dict(os.environ, _minimal_env(), clear=False):
            from orchestrator.settings import Settings
            s = Settings()
            assert s.openrouter_api_key == "sk-or-test"
            assert s.telegram_allowed_user_ids == {111, 222}
            assert s.gmail_accounts == {"test@gmail.com": "token123"}
            assert s.llm_model == "z-ai/glm-5-turbo"  # default

    def test_fails_on_missing_required(self):
        env = _minimal_env()
        del env["DATABASE_URL"]
        with patch.dict(os.environ, env, clear=False):
            # Temporarily remove DATABASE_URL if it exists in the real env
            original = os.environ.pop("DATABASE_URL", None)
            try:
                with pytest.raises(ValueError, match="DATABASE_URL"):
                    from orchestrator.settings import Settings
                    Settings()
            finally:
                if original is not None:
                    os.environ["DATABASE_URL"] = original

    def test_parses_multiple_gmail_accounts(self):
        env = _minimal_env()
        env["GMAIL_ACCOUNT_2"] = "biz@company.com"
        env["GMAIL_HOOK_TOKEN_2"] = "token456"
        with patch.dict(os.environ, env, clear=False):
            from orchestrator.settings import Settings
            s = Settings()
            assert len(s.gmail_accounts) == 2
            assert s.gmail_accounts["biz@company.com"] == "token456"
