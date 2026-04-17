"""Tests for crypto util, sender pattern matching, and rate limiter."""

import fnmatch
import pytest
from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def _fernet_key(monkeypatch):
    monkeypatch.setenv("PDF_PASSWORD_KEY", Fernet.generate_key().decode())


def test_fernet_roundtrip():
    from orchestrator.utils.crypto import encrypt, decrypt
    token = encrypt("minhasenha123")
    # Token is not the plaintext
    assert token != "minhasenha123"
    assert decrypt(token) == "minhasenha123"


def test_decrypt_invalid_token_returns_none():
    from orchestrator.utils.crypto import decrypt
    assert decrypt("not-a-valid-token") is None


def test_hash_password_is_deterministic():
    from orchestrator.utils.crypto import hash_password
    h1 = hash_password("abc")
    h2 = hash_password("abc")
    assert h1 == h2
    assert h1.startswith("sha256:")
    assert hash_password("abc") != hash_password("abd")


def test_is_configured_false_when_missing(monkeypatch):
    from orchestrator.utils import crypto
    monkeypatch.delenv("PDF_PASSWORD_KEY", raising=False)
    assert crypto.is_configured() is False


def test_sender_pattern_matches_wildcard():
    # fnmatch is what DatabaseService.get_pdf_passwords_for_sender uses
    assert fnmatch.fnmatch("cobranca@bradesco.com.br", "*@bradesco.com.br")
    assert fnmatch.fnmatch("foo@itau.com", "*@itau.com")
    assert not fnmatch.fnmatch("foo@bradesco.com.br", "*@itau.com")
    assert fnmatch.fnmatch("literal@email.com", "literal@email.com")
    assert not fnmatch.fnmatch("other@email.com", "literal@email.com")


def test_rate_limit_after_failed_attempts():
    from orchestrator.utils import pdf_ratelimit
    pdf_ratelimit.reset_all()
    assert pdf_ratelimit.is_locked(42, "*@bank.com") is False
    # 9 failures: not locked yet
    for _ in range(9):
        activated = pdf_ratelimit.record_failure(42, "*@bank.com")
        assert activated is False
    assert pdf_ratelimit.is_locked(42, "*@bank.com") is False
    # 10th triggers lockout
    activated = pdf_ratelimit.record_failure(42, "*@bank.com")
    assert activated is True
    assert pdf_ratelimit.is_locked(42, "*@bank.com") is True
    # Different account or pattern is unaffected
    assert pdf_ratelimit.is_locked(99, "*@bank.com") is False
    assert pdf_ratelimit.is_locked(42, "*@other.com") is False


def test_rate_limit_reset_on_success():
    from orchestrator.utils import pdf_ratelimit
    pdf_ratelimit.reset_all()
    for _ in range(5):
        pdf_ratelimit.record_failure(1, "x")
    pdf_ratelimit.record_success(1, "x")
    # After success, a fresh 10 failures are required to re-lock
    for i in range(9):
        assert pdf_ratelimit.record_failure(1, "x") is False
    assert pdf_ratelimit.record_failure(1, "x") is True
