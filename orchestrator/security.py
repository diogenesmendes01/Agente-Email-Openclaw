"""Security helpers shared across webhook and Telegram flows."""

from __future__ import annotations

import hmac
import os
from typing import Optional, Set, Tuple


def constant_time_equals(expected: str, provided: str) -> bool:
    """Compare two secrets without leaking timing differences."""
    if not expected or not provided:
        return False
    return hmac.compare_digest(str(expected), str(provided))


def parse_int_set(value: Optional[str]) -> Set[int]:
    """Parse a comma-separated list of integer identifiers."""
    if not value:
        return set()

    ids: Set[int] = set()
    for part in value.split(","):
        cleaned = part.strip()
        if not cleaned:
            continue
        try:
            ids.add(int(cleaned))
        except ValueError:
            continue
    return ids


def get_allowed_telegram_chat_ids() -> Set[int]:
    """Resolve chat allowlist from env, defaulting to TELEGRAM_CHAT_ID when present."""
    chat_ids = parse_int_set(os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", ""))
    if chat_ids:
        return chat_ids

    default_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not default_chat_id:
        return set()

    try:
        return {int(default_chat_id)}
    except ValueError:
        return set()


def get_allowed_telegram_user_ids() -> Set[int]:
    """Resolve Telegram user allowlist from env."""
    return parse_int_set(os.getenv("TELEGRAM_ALLOWED_USER_IDS", ""))


def is_telegram_actor_allowed(actor_id: Optional[int], chat_id: Optional[int]) -> Tuple[bool, str]:
    """
    Enforce Telegram authorization.

    Rules:
    - If a chat allowlist exists, the incoming chat must match it.
    - In group/supergroup chats (negative ids), a user allowlist is required.
    - In private chats (positive ids), fallback to actor_id == chat_id when no allowlist is configured.
    """
    allowed_chat_ids = get_allowed_telegram_chat_ids()
    allowed_user_ids = get_allowed_telegram_user_ids()

    if allowed_chat_ids and chat_id not in allowed_chat_ids:
        return False, "chat_not_allowed"

    if allowed_user_ids:
        if actor_id in allowed_user_ids:
            return True, "ok"
        return False, "user_not_allowed"

    if chat_id is not None and chat_id < 0:
        return False, "missing_allowed_users"

    if actor_id is not None and chat_id is not None and actor_id == chat_id:
        return True, "ok"

    return False, "user_not_allowed"


def extract_bearer_token(authorization_header: Optional[str]) -> str:
    """Return bearer token from Authorization header, if present."""
    if not authorization_header:
        return ""

    scheme, _, token = authorization_header.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()
