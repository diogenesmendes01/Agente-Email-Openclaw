"""PostgreSQL database service — replaces NotionService + vip_manager."""

import json
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

    async def claim_email(self, account_id: int, email_id: str) -> Optional[int]:
        """Atomically claim an email for processing.

        Uses INSERT ... ON CONFLICT DO NOTHING on the UNIQUE(account_id, email_id)
        constraint. Returns the new decision id if we won the claim, or None if
        another worker already claimed it. This is the concurrency gate — only the
        winner proceeds with side effects (playbook auto-response, actions, etc.).
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO decisions (account_id, email_id)
                   VALUES ($1, $2)
                   ON CONFLICT (account_id, email_id) DO NOTHING
                   RETURNING id""",
                account_id, email_id,
            )
            return row["id"] if row else None

    async def update_decision(self, decision_id: int, data: Dict):
        """Fill in classification/action data on a previously claimed decision row."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """UPDATE decisions
                   SET subject = $2, sender = $3, classification = $4,
                       priority = $5, category = $6, action = $7,
                       summary = $8, reasoning_tokens = $9
                   WHERE id = $1""",
                decision_id,
                data.get("subject"),
                data.get("from", data.get("sender", "")),
                data.get("classificacao", data.get("classification", "")),
                data.get("prioridade", data.get("priority", "")),
                data.get("categoria", data.get("category", "")),
                data.get("acao", data.get("action", "")),
                data.get("resumo", data.get("summary", "")),
                data.get("reasoning_tokens", 0),
            )

    async def release_claim(self, decision_id: int):
        """Delete a skeleton decision row so the email can be retried.

        Called when processing fails after claim_email() succeeded — without
        this, the UNIQUE constraint would block all future retry attempts.
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM decisions WHERE id = $1", decision_id
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

    # ── Pending Actions ──

    async def create_pending_action(self, account_id, email_id, action_type, actor_id, chat_id, message_id, state=None, topic_id=None):
        """Create a pending action with 10-minute TTL."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO pending_actions (account_id, email_id, action_type, actor_id, chat_id, topic_id, message_id, state)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                   RETURNING id""",
                account_id, email_id, action_type, actor_id, chat_id, topic_id, message_id,
                json.dumps(state or {}),
            )
            return row["id"]

    async def get_pending_action(self, email_id, action_type=None, actor_id=None, topic_id=None):
        """Get pending action by email_id, optionally filtered by action_type, actor_id and topic_id."""
        async with self._pool.acquire() as conn:
            conditions = ["email_id = $1", "expires_at > NOW()"]
            params = [email_id]
            if action_type:
                params.append(action_type)
                conditions.append(f"action_type = ${len(params)}")
            if actor_id:
                params.append(actor_id)
                conditions.append(f"actor_id = ${len(params)}")
            if topic_id:
                params.append(topic_id)
                conditions.append(f"topic_id = ${len(params)}")
            query = f"SELECT * FROM pending_actions WHERE {' AND '.join(conditions)} ORDER BY created_at DESC"
            row = await conn.fetchrow(query, *params)
            return dict(row) if row else None

    async def get_pending_by_chat(self, chat_id, action_type, actor_id=None, topic_id=None):
        """Get pending action by chat_id and action_type, optionally filtered by actor_id and topic_id."""
        async with self._pool.acquire() as conn:
            conditions = ["chat_id = $1", "action_type = $2", "expires_at > NOW()"]
            params = [chat_id, action_type]
            if actor_id:
                params.append(actor_id)
                conditions.append(f"actor_id = ${len(params)}")
            if topic_id:
                params.append(topic_id)
                conditions.append(f"topic_id = ${len(params)}")
            query = f"SELECT * FROM pending_actions WHERE {' AND '.join(conditions)} ORDER BY created_at DESC"
            row = await conn.fetchrow(query, *params)
            return dict(row) if row else None

    async def update_pending_state(self, pending_id, state):
        """Update the JSONB state of a pending action."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE pending_actions SET state = $1 WHERE id = $2",
                json.dumps(state), pending_id,
            )

    async def delete_pending_action(self, pending_id):
        """Delete a pending action (completed or cancelled)."""
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM pending_actions WHERE id = $1", pending_id)

    async def cleanup_expired_actions(self):
        """Delete expired pending actions. Returns count deleted."""
        async with self._pool.acquire() as conn:
            result = await conn.execute("DELETE FROM pending_actions WHERE expires_at < NOW()")
            return int(result.split()[-1])

    # ── Company Profiles ──

    async def get_company_profile(self, account_id):
        """Get company profile by account_id."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM company_profiles WHERE account_id = $1", account_id
            )
            return dict(row) if row else None

    async def upsert_company_profile(self, account_id, company_name, cnpj=None, tone=None, signature=None, whatsapp_url=None):
        """Create or update company profile."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO company_profiles (account_id, company_name, cnpj, tone, signature, whatsapp_url)
                   VALUES ($1, $2, $3, $4, $5, $6)
                   ON CONFLICT (account_id) DO UPDATE SET
                       company_name = EXCLUDED.company_name,
                       cnpj = COALESCE(EXCLUDED.cnpj, company_profiles.cnpj),
                       tone = COALESCE(EXCLUDED.tone, company_profiles.tone),
                       signature = COALESCE(EXCLUDED.signature, company_profiles.signature),
                       whatsapp_url = COALESCE(EXCLUDED.whatsapp_url, company_profiles.whatsapp_url),
                       updated_at = NOW()
                   RETURNING id""",
                account_id, company_name, cnpj, tone, signature, whatsapp_url,
            )
            return row["id"]

    # ── Playbooks ──

    async def get_playbooks(self, company_id, active_only=True):
        """Get playbooks for a company, ordered by priority desc."""
        async with self._pool.acquire() as conn:
            query = "SELECT * FROM playbooks WHERE company_id = $1"
            if active_only:
                query += " AND active = true"
            query += " ORDER BY priority DESC"
            rows = await conn.fetch(query, company_id)
            return [dict(r) for r in rows]

    async def create_playbook(self, company_id, trigger_description, response_template, auto_respond=True, priority=0):
        """Create a new playbook. Idempotent: updates if (company_id, trigger_description) already exists."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO playbooks (company_id, trigger_description, response_template, auto_respond, priority)
                   VALUES ($1, $2, $3, $4, $5)
                   ON CONFLICT (company_id, trigger_description) DO UPDATE SET
                       response_template = EXCLUDED.response_template,
                       auto_respond = EXCLUDED.auto_respond,
                       priority = EXCLUDED.priority,
                       updated_at = NOW()
                   RETURNING id""",
                company_id, trigger_description, response_template, auto_respond, priority,
            )
            return row["id"]

    async def delete_playbook(self, playbook_id):
        """Delete a playbook by id."""
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM playbooks WHERE id = $1", playbook_id)

    async def delete_playbook_owned(self, playbook_id, company_id) -> bool:
        """Delete a playbook only if it belongs to the given company. Returns True if deleted."""
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM playbooks WHERE id = $1 AND company_id = $2",
                playbook_id, company_id,
            )
            return result != "DELETE 0"

    # ── Domain Rules ──

    async def get_domain_rules(self, company_id):
        """Get domain rules for a company."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM domain_rules WHERE company_id = $1", company_id
            )
            return [dict(r) for r in rows]

    # ── Account by Topic ──

    async def get_account_by_topic(self, topic_id):
        """Get account by Telegram topic ID."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM accounts WHERE telegram_topic_id = $1", topic_id
            )
            return dict(row) if row else None
