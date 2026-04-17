"""In-memory rate limiter for PDF password attempts.

Protects against abuse (e.g. a malicious sender flooding the agent with
password-protected PDFs). 10 failed attempts within 10 minutes per
(account_id, sender_pattern) triggers a 30-minute lockout.

State is process-local; for multi-worker deployments the `locked_until`
column on pdf_passwords persists the lockout across restarts.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock
from typing import Deque, Dict, Tuple

WINDOW_SECONDS = 10 * 60          # 10 minutes
MAX_FAILURES = 10
LOCK_MINUTES = 30

_failures: Dict[Tuple[int, str], Deque[float]] = defaultdict(deque)
_lockouts: Dict[Tuple[int, str], float] = {}
_mutex = Lock()


def _prune(key: Tuple[int, str], now: float):
    q = _failures[key]
    while q and now - q[0] > WINDOW_SECONDS:
        q.popleft()


def is_locked(account_id: int, sender_pattern: str) -> bool:
    """Return True if this (account, pattern) is currently locked out."""
    key = (account_id, sender_pattern)
    now = time.time()
    with _mutex:
        until = _lockouts.get(key)
        if until and now < until:
            return True
        if until and now >= until:
            _lockouts.pop(key, None)
    return False


def record_failure(account_id: int, sender_pattern: str) -> bool:
    """Record a failed password attempt. Returns True if lockout just activated."""
    key = (account_id, sender_pattern)
    now = time.time()
    with _mutex:
        _prune(key, now)
        _failures[key].append(now)
        if len(_failures[key]) >= MAX_FAILURES:
            _lockouts[key] = now + LOCK_MINUTES * 60
            _failures[key].clear()
            return True
    return False


def record_success(account_id: int, sender_pattern: str):
    """Reset counters on successful password use."""
    key = (account_id, sender_pattern)
    with _mutex:
        _failures.pop(key, None)
        _lockouts.pop(key, None)


def reset_all():
    """Test helper — wipe all state."""
    with _mutex:
        _failures.clear()
        _lockouts.clear()
