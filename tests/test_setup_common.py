"""Tests for setup_steps/common.py — wizard UI helpers."""
import pytest
from unittest.mock import patch, MagicMock


class TestAsk:
    def test_returns_user_input(self):
        from setup_steps.common import ask
        with patch("builtins.input", return_value="meu_valor"):
            result = ask("Pergunta")
        assert result == "meu_valor"

    def test_returns_default_on_empty_input(self):
        from setup_steps.common import ask
        with patch("builtins.input", return_value=""):
            result = ask("Pergunta", default="default_val")
        assert result == "default_val"

    def test_strips_whitespace(self):
        from setup_steps.common import ask
        with patch("builtins.input", return_value="  valor  "):
            result = ask("Pergunta")
        assert result == "valor"


class TestAskPassword:
    def test_returns_password(self):
        from setup_steps.common import ask_password
        with patch("getpass.getpass", return_value="senha123"):
            result = ask_password("Senha")
        assert result == "senha123"


class TestConfirm:
    def test_yes_returns_true(self):
        from setup_steps.common import confirm
        with patch("builtins.input", return_value="s"):
            assert confirm("Continuar?") is True

    def test_no_returns_false(self):
        from setup_steps.common import confirm
        with patch("builtins.input", return_value="n"):
            assert confirm("Continuar?") is False

    def test_empty_returns_default_true(self):
        from setup_steps.common import confirm
        with patch("builtins.input", return_value=""):
            assert confirm("Continuar?", default=True) is True

    def test_empty_returns_default_false(self):
        from setup_steps.common import confirm
        with patch("builtins.input", return_value=""):
            assert confirm("Continuar?", default=False) is False


class TestAskChoice:
    def test_returns_selected_choice(self):
        from setup_steps.common import ask_choice
        with patch("builtins.input", return_value="2"):
            result = ask_choice("Escolha:", ["Opção A", "Opção B", "Opção C"])
        assert result == 1  # 0-indexed

    def test_invalid_then_valid(self):
        from setup_steps.common import ask_choice
        with patch("builtins.input", side_effect=["99", "abc", "1"]):
            result = ask_choice("Escolha:", ["A", "B"])
        assert result == 0
