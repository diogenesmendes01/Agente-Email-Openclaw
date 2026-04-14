"""Tests for setup_steps/accounts.py."""
import pytest
from unittest.mock import patch, MagicMock, call


class TestCreateAccount:
    def test_inserts_account(self):
        from setup_steps.accounts import create_account
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = (1,)
        account_id = create_account(mock_conn, "test@g.com", "GMAIL_HOOK_TOKEN_1", 123)
        assert account_id == 1

    def test_returns_existing_on_conflict(self):
        from setup_steps.accounts import create_account
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = (5,)
        account_id = create_account(mock_conn, "test@g.com", "TOK", None)
        assert account_id == 5


    def test_topic_id_passed_to_create_account(self):
        """create_account receives the per-account topic_id (not TELEGRAM_CHAT_ID)."""
        from setup_steps.accounts import create_account
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = (1,)
        create_account(mock_conn, "test@g.com", "GMAIL_HOOK_TOKEN_1", 42)
        # The SQL should receive topic_id=42
        sql_args = mock_cursor.execute.call_args[0][1]
        assert sql_args == ("test@g.com", "GMAIL_HOOK_TOKEN_1", 42)

    def test_topic_id_none_when_not_provided(self):
        """create_account should accept None for topic_id."""
        from setup_steps.accounts import create_account
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = (1,)
        create_account(mock_conn, "test@g.com", "TOK", None)
        sql_args = mock_cursor.execute.call_args[0][1]
        assert sql_args[2] is None


class TestCreateCompanyProfile:
    def test_inserts_profile(self):
        from setup_steps.accounts import create_company_profile
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = (1,)
        profile_id = create_company_profile(
            mock_conn, 1, "CodeWave", "12.345/0001-90", "formal", "Att,\nEquipe", None
        )
        assert profile_id == 1
