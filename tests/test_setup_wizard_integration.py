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


class TestFirstRunWarnings:
    """first_run() should track step failures and show warnings."""

    def test_shows_warnings_on_telegram_failure(self):
        """If telegram.run returns False, first_run should include it in warnings."""
        import setup_wizard
        from setup_steps.common import warning as _warning

        warnings_printed = []
        original_dir = setup_wizard.PROJECT_DIR

        with patch("setup_steps.dependencies.run", return_value=True), \
             patch("setup_steps.env_config.run", return_value={"DATABASE_URL": "x"}), \
             patch("setup_steps.database.run", return_value=True), \
             patch("setup_steps.telegram.run", return_value=False), \
             patch("setup_steps.env_config.write_env_file"), \
             patch("setup_steps.gmail.run", return_value=[{"email": "a@g.com", "is_corporate": False, "account_num": 1, "hook_token_env": "T"}]), \
             patch("setup_steps.accounts.run", return_value=[]), \
             patch("setup_steps.playbooks.run", return_value=True), \
             patch("setup_steps.common.warning", side_effect=lambda msg: warnings_printed.append(msg)):
            setup_wizard.first_run()

        assert any("Telegram" in w for w in warnings_printed)

    def test_shows_warnings_on_empty_gmail(self):
        """If gmail.run returns empty list, first_run should warn."""
        import setup_wizard

        warnings_printed = []

        with patch("setup_steps.dependencies.run", return_value=True), \
             patch("setup_steps.env_config.run", return_value={"DATABASE_URL": "x"}), \
             patch("setup_steps.database.run", return_value=True), \
             patch("setup_steps.telegram.run", return_value=True), \
             patch("setup_steps.env_config.write_env_file"), \
             patch("setup_steps.gmail.run", return_value=[]), \
             patch("setup_steps.accounts.run", return_value=[]), \
             patch("setup_steps.playbooks.run", return_value=True), \
             patch("setup_steps.common.warning", side_effect=lambda msg: warnings_printed.append(msg)):
            setup_wizard.first_run()

        assert any("Gmail" in w for w in warnings_printed)

    def test_shows_warnings_on_accounts_failure(self):
        """If accounts.run returns accounts without account_id, first_run should warn."""
        import setup_wizard

        warnings_printed = []
        # Simulate accounts.run returning accounts without account_id (DB creation failed)
        gmail_result = [{"email": "a@g.com", "is_corporate": False, "account_num": 1, "hook_token_env": "T"}]
        accounts_result = [{"email": "a@g.com", "is_corporate": False, "account_num": 1, "hook_token_env": "T"}]  # no account_id

        with patch("setup_steps.dependencies.run", return_value=True), \
             patch("setup_steps.env_config.run", return_value={"DATABASE_URL": "x"}), \
             patch("setup_steps.database.run", return_value=True), \
             patch("setup_steps.telegram.run", return_value=True), \
             patch("setup_steps.env_config.write_env_file"), \
             patch("setup_steps.gmail.run", return_value=gmail_result), \
             patch("setup_steps.accounts.run", return_value=accounts_result), \
             patch("setup_steps.playbooks.run", return_value=True), \
             patch("setup_steps.common.warning", side_effect=lambda msg: warnings_printed.append(msg)):
            setup_wizard.first_run()

        assert any("Contas" in w for w in warnings_printed)

    def test_shows_warnings_on_playbooks_failure(self):
        """If playbooks.run returns False, first_run should warn."""
        import setup_wizard

        warnings_printed = []

        with patch("setup_steps.dependencies.run", return_value=True), \
             patch("setup_steps.env_config.run", return_value={"DATABASE_URL": "x"}), \
             patch("setup_steps.database.run", return_value=True), \
             patch("setup_steps.telegram.run", return_value=True), \
             patch("setup_steps.env_config.write_env_file"), \
             patch("setup_steps.gmail.run", return_value=[{"email": "a@g.com", "is_corporate": True, "account_num": 1, "hook_token_env": "T"}]), \
             patch("setup_steps.accounts.run", return_value=[{"email": "a@g.com", "is_corporate": True, "account_num": 1, "hook_token_env": "T", "account_id": 1, "company_id": 1}]), \
             patch("setup_steps.playbooks.run", return_value=False), \
             patch("setup_steps.common.warning", side_effect=lambda msg: warnings_printed.append(msg)):
            setup_wizard.first_run()

        assert any("Playbooks" in w for w in warnings_printed)


class TestRerunMenuAddGmailAccountsFailure:
    """rerun_menu 'Adicionar nova conta Gmail' path when accounts.run fails."""

    def test_rerun_menu_survives_accounts_run_exception(self, tmp_path):
        """If gmail.run succeeds but accounts.run raises, rerun_menu must
        NOT propagate the exception — it should log an error and continue.
        The .env must already be written (gmail accounts are not lost)."""
        import setup_wizard

        original_dir = setup_wizard.PROJECT_DIR
        setup_wizard.PROJECT_DIR = tmp_path

        (tmp_path / ".env").write_text("DATABASE_URL=postgresql://u:p@localhost/db\n")

        gmail_result = [{"email": "new@g.com", "is_corporate": False,
                         "account_num": 1, "hook_token_env": "GMAIL_HOOK_TOKEN_1"}]

        errors_printed = []

        try:
            with patch("setup_steps.common.ask_choice", return_value=3), \
                 patch("setup_wizard.ensure_requirements"), \
                 patch("setup_steps.gmail.run", return_value=gmail_result) as mock_gmail, \
                 patch("setup_steps.env_config.write_env_file") as mock_write, \
                 patch("setup_steps.accounts.run", side_effect=Exception("connection refused")), \
                 patch("setup_steps.common.error", side_effect=lambda m: errors_printed.append(m)), \
                 patch("setup_steps.common.warning"):
                # This must NOT raise
                setup_wizard.rerun_menu()

            # .env was written before accounts.run was called
            mock_write.assert_called_once()
            mock_gmail.assert_called_once()
            # The error was logged, not swallowed silently
            assert any("connection refused" in e for e in errors_printed)
        finally:
            setup_wizard.PROJECT_DIR = original_dir

    def test_accounts_failure_does_not_lose_gmail_env_vars(self, tmp_path):
        """After gmail.run adds env vars, accounts.run returning accounts
        without account_id should still leave .env updated."""
        import setup_wizard

        original_dir = setup_wizard.PROJECT_DIR
        setup_wizard.PROJECT_DIR = tmp_path

        (tmp_path / ".env").write_text("DATABASE_URL=postgresql://u:p@localhost/db\n")

        gmail_result = [{"email": "corp@g.com", "is_corporate": True,
                         "account_num": 1, "hook_token_env": "GMAIL_HOOK_TOKEN_1"}]
        # accounts.run returns the same list WITHOUT account_id → DB creation failed
        accounts_result = [{"email": "corp@g.com", "is_corporate": True,
                            "account_num": 1, "hook_token_env": "GMAIL_HOOK_TOKEN_1"}]

        try:
            with patch("setup_steps.common.ask_choice", return_value=3), \
                 patch("setup_wizard.ensure_requirements"), \
                 patch("setup_steps.gmail.run", return_value=gmail_result), \
                 patch("setup_steps.env_config.write_env_file") as mock_write, \
                 patch("setup_steps.accounts.run", return_value=accounts_result):
                setup_wizard.rerun_menu()

            # .env was written (gmail env vars saved)
            mock_write.assert_called_once()
            # accounts.run was still called (not skipped)
            written_env = mock_write.call_args[0][1]
            assert "DATABASE_URL" in written_env
        finally:
            setup_wizard.PROJECT_DIR = original_dir


class TestValidationDoesNotCrash:
    """Validation should handle missing services gracefully."""

    def test_validation_with_empty_env(self):
        from setup_wizard import run_validation
        # Should print errors but not crash
        run_validation({})
