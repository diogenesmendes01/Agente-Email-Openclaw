"""Deterministic detection of non-replyable emails.

Layer A: regex on sender address (no-reply, mailer-daemon, etc.)
Layer B: classifier-driven category check
"""
from __future__ import annotations

import re
from typing import Optional

_NO_REPLY_LOCAL_PARTS = re.compile(
    r"""(?ix)
    ^(
        no[-_]?reply
      | do[-_]?not[-_]?reply
      | mailer[-_]?daemon
      | postmaster
      | bounces?
      | notifications?
      | alerts?
      | news
      | newsletter
      | automated
      | system
    )(\+.*)?$
    """,
)

_NON_REPLYABLE_CATEGORIES = frozenset({
    "newsletter",
    "promocao",
    "notificacao_automatica",
    "transacional",
})


def _extract_local_part(addr: str) -> str:
    if not addr or not isinstance(addr, str):
        return ""
    s = addr.strip()
    m = re.search(r"<([^>]+)>", s)
    if m:
        s = m.group(1).strip()
    if "@" not in s:
        return ""
    return s.split("@", 1)[0].strip().lower()


def is_no_reply_sender(from_addr: Optional[str]) -> bool:
    """True if the sender address looks like an automated/no-reply mailbox."""
    local = _extract_local_part(from_addr or "")
    if not local:
        return False
    return bool(_NO_REPLY_LOCAL_PARTS.match(local))


def is_non_replyable_category(category: Optional[str]) -> bool:
    """True if the classifier category is one we should never draft a reply for."""
    if not category or not isinstance(category, str):
        return False
    return category.strip().lower() in _NON_REPLYABLE_CATEGORIES
