"""Integration smoke tests for the setup wizard modules."""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestDetectState:
    def test_fresh_install(self, tmp_path):
        import setup_wizard
        original = setup_wizard.PROJECT_DIR
        setup_wizard.PROJECT_DIR = tmp_path
        try:
            state = setup_wizard.detect_state()
            assert state["env_exists"] is False
            assert state["credentials_exist"] is False
        finally:
            setup_wizard.PROJECT_DIR = original

    def test_existing_install(self, tmp_path):
        (tmp_path / ".env").write_text("X=1\n")
        (tmp_path / "credentials").mkdir()
        (tmp_path / "credentials" / "client_secret.json").write_text("{}")
        import setup_wizard
        original = setup_wizard.PROJECT_DIR
        setup_wizard.PROJECT_DIR = tmp_path
        try:
            state = setup_wizard.detect_state()
            assert state["env_exists"] is True
            assert state["credentials_exist"] is True
        finally:
            setup_wizard.PROJECT_DIR = original


class TestEnvRoundTrip:
    """Test that env_config can write and re-read .env correctly."""

    def test_write_and_read_back(self, tmp_path):
        from setup_steps.env_config import write_env_file, parse_existing_env
        data = {
            "DATABASE_URL": "postgresql://u:p@localhost:5432/db",
            "TELEGRAM_BOT_TOKEN": "123:ABC",
            "GMAIL_ACCOUNT_1": "test@gmail.com",
            "GMAIL_HOOK_TOKEN_1": "hextoken",
        }
        env_path = tmp_path / ".env"
        write_env_file(env_path, data)
        loaded = parse_existing_env(env_path)
        assert loaded["DATABASE_URL"] == data["DATABASE_URL"]
        assert loaded["TELEGRAM_BOT_TOKEN"] == data["TELEGRAM_BOT_TOKEN"]
        assert loaded["GMAIL_ACCOUNT_1"] == data["GMAIL_ACCOUNT_1"]


class TestBootstrapDeps:
    """Test that ensure_bootstrap_deps uses correct module names."""

    def test_maps_python_dotenv_to_dotenv(self):
        """python-dotenv pip package should import as 'dotenv', not 'python_dotenv'."""
        from setup_wizard import ensure_bootstrap_deps
        with patch("builtins.__import__", side_effect=lambda name, *a, **kw: None) as mock_import:
            with patch("subprocess.check_call"):
                ensure_bootstrap_deps()
        # Should try importing 'dotenv' (not 'python_dotenv')
        import_names = [c[0][0] for c in mock_import.call_args_list]
        assert "dotenv" in import_names
        assert "python_dotenv" not in import_names


class TestValidationDoesNotCrash:
    """Validation should handle missing services gracefully."""

    def test_validation_with_empty_env(self):
        from setup_wizard import run_validation
        # Should print errors but not crash
        run_validation({})
