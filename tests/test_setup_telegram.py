"""Tests for setup_steps/telegram.py."""
import pytest
from unittest.mock import patch, MagicMock


class TestValidateToken:
    def test_valid_token(self):
        from setup_steps.telegram import validate_token
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "ok": True,
            "result": {"username": "MyBot", "first_name": "My Bot"},
        }
        with patch("requests.get", return_value=mock_resp):
            result = validate_token("123:ABC")
        assert result["username"] == "MyBot"

    def test_invalid_token(self):
        from setup_steps.telegram import validate_token
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.json.return_value = {"ok": False}
        with patch("requests.get", return_value=mock_resp):
            result = validate_token("bad_token")
        assert result is None


class TestDiscoverChatId:
    def test_finds_chat_id(self):
        from setup_steps.telegram import discover_chat_id
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "ok": True,
            "result": [
                {"message": {"chat": {"id": -100123456, "title": "Meu Grupo"}}}
            ],
        }
        with patch("requests.get", return_value=mock_resp):
            chat_id, title = discover_chat_id("123:ABC")
        assert chat_id == -100123456
        assert title == "Meu Grupo"

    def test_no_updates(self):
        from setup_steps.telegram import discover_chat_id
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True, "result": []}
        with patch("requests.get", return_value=mock_resp):
            chat_id, title = discover_chat_id("123:ABC")
        assert chat_id is None
