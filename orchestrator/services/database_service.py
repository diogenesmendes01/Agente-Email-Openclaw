"""PostgreSQL database service — replaces NotionService + vip_manager."""

import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


class DatabaseService:
    """Async PostgreSQL client using asyncpg connection pool."""

    def __init__(self, pool):
        self._pool = pool

    async def is_connected(self) -> bool:
        try:
            async with self._pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception:
            return False

    # ── Accounts ──

    async def get_account(self, email: str) -> Optional[Dict]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM accounts WHERE email = $1", email
            )
            return dict(row) if row else None

    async def get_account_by_id(self, account_id: int) -> Optional[Dict]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM accounts WHERE id = $1", account_id
            )
            return dict(row) if row else None

    async def get_account_config(self, email: str) -> Dict:
        """Returns config dict compatible with old NotionService format."""
        async with self._pool.acquire() as conn:
            account = await conn.fetchrow(
                "SELECT * FROM accounts WHERE email = $1", email
            )
            if not account:
                return self._default_config()

            vips = await conn.fetch(
                "SELECT sender_email FROM vip_list WHERE account_id = $1",
                account["id"],
            )
            return {
                "vips": [r["sender_email"] for r in vips],
                "urgency_words": [],
                "ignore_words": [],
                "projetos": [],
                "telegram_topic": account["telegram_topic_id"],
                "auto_reply": False,
            }

    def _default_config(self) -> Dict:
        return {
            "vips": [],
            "urgency_words": ["urgente", "deadline", "vencimento"],
            "ignore_words": ["newsletter", "unsubscribe"],
            "projetos": [],
            "telegram_topic": None,
            "auto_reply": False,
        }

    # ── VIP ──

    async def add_vip(
        self, account_id: int, sender_email: str,
        sender_name: str = "", min_urgency: str = "high"
    ) -> bool:
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO vip_list (account_id, sender_email, sender_name, min_urgency)
                       VALUES ($1, $2, $3, $4)
                       ON CONFLICT (account_id, sender_email) DO NOTHING""",
                    account_id, sender_email, sender_name, min_urgency,
                )
            return True
        except Exception as e:
            logger.error(f"Error adding VIP: {e}")
            return False

    async def remove_vip(self, account_id: int, sender_email: str) -> bool:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM vip_list WHERE account_id = $1 AND sender_email = $2",
                account_id, sender_email,
            )
            return result != "DELETE 0"

    async def is_vip(self, account_id: int, sender_email: str) -> bool:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM vip_list WHERE account_id = $1 AND sender_email = $2)",
                account_id, sender_email,
            )

    async def get_vips(self, account_id: int) -> List[Dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM vip_list WHERE account_id = $1", account_id
            )
            return [dict(r) for r in rows]

    # ── Blacklist ──

    async def add_to_blacklist(
        self, account_id: int, sender_email: str, reason: str = ""
    ) -> bool:
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO blacklist (account_id, sender_email, reason)
                       VALUES ($1, $2, $3)
                       ON CONFLICT (account_id, sender_email) DO NOTHING""",
                    account_id, sender_email, reason,
                )
            return True
        except Exception as e:
            logger.error(f"Error adding to blacklist: {e}")
            return False

    async def remove_from_blacklist(self, account_id: int, sender_email: str) -> bool:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM blacklist WHERE account_id = $1 AND sender_email = $2",
                account_id, sender_email,
            )
            return result != "DELETE 0"

    async def is_blacklisted(self, account_id: int, sender_email: str) -> bool:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM blacklist WHERE account_id = $1 AND sender_email = $2)",
                account_id, sender_email,
            )

    # ── Feedback ──

    async def save_feedback(
        self, account_id: int, email_id: str, sender: str,
        original_urgency: str, corrected_urgency: str, keywords: list
    ) -> int:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                """INSERT INTO feedback (account_id, email_id, sender, original_urgency, corrected_urgency, keywords)
                   VALUES ($1, $2, $3, $4, $5, $6)
                   RETURNING id""",
                account_id, email_id, sender, original_urgency, corrected_urgency, keywords,
            )

    async def get_feedback(self, account_id: int, limit: int = 100) -> List[Dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM feedback WHERE account_id = $1 ORDER BY created_at DESC LIMIT $2",
                account_id, limit,
            )
            return [dict(r) for r in rows]

    # ── Decisions ──

    async def log_decision(self, data: Dict) -> int:
        """Log email processing decision."""
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                """INSERT INTO decisions
                   (account_id, email_id, subject, sender, classification,
                    priority, category, action, summary, reasoning_tokens)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                   RETURNING id""",
                data.get("account_id"),
                data.get("email_id"),
                data.get("subject"),
                data.get("from", data.get("sender", "")),
                data.get("classificacao", data.get("classification", "")),
                data.get("prioridade", data.get("priority", "")),
                data.get("categoria", data.get("category", "")),
                data.get("acao", data.get("action", "")),
                data.get("resumo", data.get("summary", "")),
                data.get("reasoning_tokens", 0),
            )

    # ── Tasks ──

    async def create_task(
        self, account_id: int, title: str,
        priority: str = "Média", email_id: str = ""
    ) -> int:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                """INSERT INTO tasks (account_id, email_id, title, priority)
                   VALUES ($1, $2, $3, $4)
                   RETURNING id""",
                account_id, email_id, title, priority,
            )

    # ── History IDs ──

    async def get_history_id(self, account_id: int) -> Optional[str]:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT history_id FROM history_ids WHERE account_id = $1",
                account_id,
            )

    async def save_history_id(self, account_id: int, history_id: str):
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO history_ids (account_id, history_id, updated_at)
                   VALUES ($1, $2, NOW())
                   ON CONFLICT (account_id)
                   DO UPDATE SET history_id = $2, updated_at = NOW()""",
                account_id, history_id,
            )

    # ── Learning Counter ──

    async def get_learning_counter(self, account_id: int) -> int:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT learning_counter FROM accounts WHERE id = $1",
                account_id,
            ) or 0

    async def update_learning_counter(self, account_id: int, count: int):
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE accounts SET learning_counter = $1 WHERE id = $2",
                count, account_id,
            )
