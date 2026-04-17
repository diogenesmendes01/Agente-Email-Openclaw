"""Symmetric encryption utility for sensitive per-account secrets.

Uses Fernet (AES-128 CBC + HMAC). The key is read from the PDF_PASSWORD_KEY
environment variable. To generate one:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Used for:
- pdf_passwords.password_encrypted
- account_documents.cpf_encrypted / cnpj_encrypted / birthdate_encrypted
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


class CryptoError(RuntimeError):
    """Raised when encryption/decryption is attempted without a configured key."""


def _get_fernet() -> Fernet:
    key = os.getenv("PDF_PASSWORD_KEY", "").strip()
    if not key:
        raise CryptoError(
            "PDF_PASSWORD_KEY environment variable is not set. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as exc:
        raise CryptoError(f"Invalid PDF_PASSWORD_KEY: {exc}") from exc


def encrypt(plaintext: str) -> str:
    """Encrypt a string, returning the Fernet token as str."""
    if plaintext is None:
        return None
    f = _get_fernet()
    token = f.encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt(ciphertext: str) -> Optional[str]:
    """Decrypt a Fernet token. Returns None if the token is invalid/corrupted."""
    if not ciphertext:
        return None
    try:
        f = _get_fernet()
        return f.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        logger.warning(f"Failed to decrypt ciphertext: {exc}")
        return None


def hash_password(password: str) -> str:
    """Return sha256:<hex> of the password. Used for telemetry only —
    never stored as credential, only to correlate attempts without leaking
    the plaintext.
    """
    digest = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def is_configured() -> bool:
    """Return True if PDF_PASSWORD_KEY is set and appears valid."""
    try:
        _get_fernet()
        return True
    except CryptoError:
        return False
