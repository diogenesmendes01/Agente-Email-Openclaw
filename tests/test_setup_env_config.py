"""Tests for setup_steps/env_config.py."""
import pytest
from pathlib import Path
from unittest.mock import patch, call


class TestBuildDatabaseUrl:
    def test_builds_url_from_parts(self):
        from setup_steps.env_config import build_database_url
        url = build_database_url("localhost", "5432", "mydb", "user", "pass")
        assert url == "postgresql://user:pass@localhost:5432/mydb"

    def test_special_chars_in_password(self):
        from setup_steps.env_config import build_database_url
        url = build_database_url("localhost", "5432", "db", "u", "p@ss/w%rd")
        assert "p%40ss%2Fw%25rd" in url


class TestParseExistingEnv:
    def test_loads_existing_values(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("DATABASE_URL=postgresql://x\nTELEGRAM_BOT_TOKEN=tok123\n")
        from setup_steps.env_config import parse_existing_env
        values = parse_existing_env(env_file)
        assert values["DATABASE_URL"] == "postgresql://x"
        assert values["TELEGRAM_BOT_TOKEN"] == "tok123"

    def test_returns_empty_if_no_file(self, tmp_path):
        from setup_steps.env_config import parse_existing_env
        values = parse_existing_env(tmp_path / ".env")
        assert values == {}


class TestWriteEnvFile:
    def test_writes_env_file(self, tmp_path):
        from setup_steps.env_config import write_env_file
        env_path = tmp_path / ".env"
        data = {"DATABASE_URL": "postgresql://x", "TELEGRAM_BOT_TOKEN": "tok"}
        write_env_file(env_path, data)
        content = env_path.read_text()
        assert "DATABASE_URL=postgresql://x" in content
        assert "TELEGRAM_BOT_TOKEN=tok" in content

    def test_creates_backup_if_exists(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("OLD=value\n")
        from setup_steps.env_config import write_env_file
        write_env_file(env_path, {"NEW": "val"})
        backup = tmp_path / ".env.backup"
        assert backup.exists()
        assert backup.read_text() == "OLD=value\n"


class TestTelegramTokenMasked:
    """TELEGRAM_BOT_TOKEN must be collected via ask_password, not ask."""

    def test_bot_token_uses_ask_password(self, tmp_path):
        """run() should call ask_password for the Telegram bot token."""
        from setup_steps import env_config

        original_dir = env_config.Path  # just need to mock calls
        with patch("setup_steps.env_config.ask", return_value="default_val"), \
             patch("setup_steps.env_config.ask_password", return_value="secret_token") as mock_ask_pw, \
             patch("setup_steps.env_config.confirm", return_value=False), \
             patch("setup_steps.env_config.write_env_file"):
            env = env_config.run(tmp_path)

        # ask_password should have been called with a prompt containing "Token do Bot"
        bot_token_calls = [c for c in mock_ask_pw.call_args_list if "Token do Bot" in str(c)]
        assert len(bot_token_calls) >= 1, "TELEGRAM_BOT_TOKEN should use ask_password"
        assert env["TELEGRAM_BOT_TOKEN"] == "secret_token"
