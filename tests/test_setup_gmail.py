"""Tests for setup_steps/gmail.py."""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestCheckClientSecret:
    def test_returns_true_if_exists(self, tmp_path):
        (tmp_path / "credentials").mkdir()
        (tmp_path / "credentials" / "client_secret.json").write_text("{}")
        from setup_steps.gmail import check_client_secret
        assert check_client_secret(tmp_path) is True

    def test_returns_false_if_missing(self, tmp_path):
        from setup_steps.gmail import check_client_secret
        assert check_client_secret(tmp_path) is False


class TestCountExistingAccounts:
    def test_counts_gmail_accounts(self):
        from setup_steps.gmail import count_existing_accounts
        env = {
            "GMAIL_ACCOUNT_1": "a@g.com",
            "GMAIL_HOOK_TOKEN_1": "tok1",
            "GMAIL_ACCOUNT_2": "b@g.com",
            "GMAIL_HOOK_TOKEN_2": "tok2",
            "OTHER_KEY": "val",
        }
        assert count_existing_accounts(env) == 2

    def test_zero_if_no_accounts(self):
        from setup_steps.gmail import count_existing_accounts
        assert count_existing_accounts({}) == 0
