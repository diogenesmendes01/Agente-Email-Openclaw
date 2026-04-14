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

    def test_prefers_group_over_private(self):
        """discover_chat_id should prefer group/supergroup chats over private DMs."""
        from setup_steps.telegram import discover_chat_id
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "ok": True,
            "result": [
                {"message": {"chat": {"id": 111, "type": "private", "first_name": "User"}}},
                {"message": {"chat": {"id": -100999, "type": "supergroup", "title": "Grupo"}}},
            ],
        }
        with patch("requests.get", return_value=mock_resp):
            chat_id, title = discover_chat_id("123:ABC")
        assert chat_id == -100999
        assert title == "Grupo"

    def test_falls_back_to_private_if_no_group(self):
        """If no group chat exists, discover_chat_id should return a private chat."""
        from setup_steps.telegram import discover_chat_id
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "ok": True,
            "result": [
                {"message": {"chat": {"id": 111, "type": "private", "first_name": "User"}}},
            ],
        }
        with patch("requests.get", return_value=mock_resp):
            chat_id, title = discover_chat_id("123:ABC")
        assert chat_id == 111


class TestFlushOldUpdates:
    def test_flushes_with_offset(self):
        """_flush_old_updates should call getUpdates twice: once to get last id, once to confirm."""
        from setup_steps.telegram import _flush_old_updates
        responses = [
            MagicMock(json=MagicMock(return_value={
                "ok": True, "result": [{"update_id": 500}]
            })),
            MagicMock(json=MagicMock(return_value={"ok": True, "result": []})),
        ]
        with patch("requests.get", side_effect=responses) as mock_get:
            _flush_old_updates("123:ABC")
        assert mock_get.call_count == 2
        # Second call should use offset = last_id + 1
        second_call_params = mock_get.call_args_list[1][1].get("params", {})
        assert second_call_params.get("offset") == 501

    def test_noop_when_no_updates(self):
        """_flush_old_updates should not error when there are no updates."""
        from setup_steps.telegram import _flush_old_updates
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True, "result": []}
        with patch("requests.get", return_value=mock_resp) as mock_get:
            _flush_old_updates("123:ABC")
        assert mock_get.call_count == 1
