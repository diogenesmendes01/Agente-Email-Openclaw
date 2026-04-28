"""Redact sensitive keys from dicts before logging.

Used in webhook handlers and any place where external payloads might
contain credentials.
"""
from __future__ import annotations

from typing import Any

_SENSITIVE_KEYS = frozenset({
    "token",
    "authorization",
    "password",
    "secret",
    "api_key",
    "access_token",
    "refresh_token",
    "cookie",
})

_REDACTED = "<REDACTED>"


def redact_sensitive(value: Any) -> Any:
    """Return a deep copy with sensitive keys replaced by '<REDACTED>'.

    - Keys are matched case-insensitively against _SENSITIVE_KEYS
    - Recurses into nested dicts and lists
    - Non-dict, non-list values pass through unchanged (None, str, int, etc.)
    - Does NOT mutate the input
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and k.lower() in _SENSITIVE_KEYS:
                out[k] = _REDACTED
            else:
                out[k] = redact_sensitive(v)
        return out
    if isinstance(value, list):
        return [redact_sensitive(v) for v in value]
    return value
