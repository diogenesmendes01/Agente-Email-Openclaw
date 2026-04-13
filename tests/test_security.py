"""Tests for shared security helpers."""

from unittest.mock import patch

from orchestrator.security import (
    constant_time_equals,
    extract_bearer_token,
    is_telegram_actor_allowed,
    is_valid_account,
    is_valid_email_id,
    parse_int_set,
    truncate_identifier,
)


class TestParseIntSet:
    def test_parses_comma_separated_ids(self):
        assert parse_int_set("123, 456, -100789") == {123, 456, -100789}

    def test_ignores_invalid_values(self):
        assert parse_int_set("123, nope, , 456") == {123, 456}


class TestTelegramAuthorization:
    def test_private_chat_falls_back_to_same_user(self):
        with patch.dict("os.environ", {"TELEGRAM_CHAT_ID": "123"}, clear=True):
            allowed, reason = is_telegram_actor_allowed(123, 123)
        assert allowed is True
        assert reason == "ok"

    def test_group_requires_allowed_users(self):
        with patch.dict("os.environ", {"TELEGRAM_CHAT_ID": "-1001"}, clear=True):
            allowed, reason = is_telegram_actor_allowed(123, -1001)
        assert allowed is False
        assert reason == "missing_allowed_users"

    def test_group_allows_only_configured_users(self):
        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_CHAT_ID": "-1001",
                "TELEGRAM_ALLOWED_USER_IDS": "123,456",
            },
            clear=True,
        ):
            allowed, reason = is_telegram_actor_allowed(456, -1001)
        assert allowed is True
        assert reason == "ok"

    def test_rejects_unlisted_chat(self):
        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_ALLOWED_CHAT_IDS": "-2002",
                "TELEGRAM_ALLOWED_USER_IDS": "123",
            },
            clear=True,
        ):
            allowed, reason = is_telegram_actor_allowed(123, -1001)
        assert allowed is False
        assert reason == "chat_not_allowed"


class TestSecretHelpers:
    def test_constant_time_compare_requires_both_values(self):
        assert constant_time_equals("abc", "abc") is True
        assert constant_time_equals("abc", "") is False

    def test_extracts_bearer_token(self):
        assert extract_bearer_token("Bearer test-token") == "test-token"
        assert extract_bearer_token("Basic abc") == ""


class TestIdentifierValidation:
    def test_accepts_expected_email_ids(self):
        assert is_valid_email_id("18c28f9aBC_-1234") is True

    def test_rejects_malformed_email_ids(self):
        assert is_valid_email_id("../etc/passwd") is False
        assert is_valid_email_id("short") is False

    def test_accepts_valid_accounts(self):
        assert is_valid_account("user@example.com") is True

    def test_rejects_invalid_accounts(self):
        assert is_valid_account("not-an-email") is False
        assert is_valid_account("user @example.com") is False

    def test_truncates_identifiers_for_logs(self):
        assert truncate_identifier("1234567890abcdef") == "12345678..."
