# Phase 1: Foundation (PostgreSQL + Settings + PDF) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Notion and JSON file persistence with PostgreSQL, unify configuration into a Settings object, and add PDF attachment reading to the email pipeline.

**Architecture:** Add PostgreSQL container to Docker Compose. Create `DatabaseService` as async wrapper using `asyncpg` connection pool. Replace all `NotionService` and `vip_manager` calls in `email_processor.py` and `main.py` with `DatabaseService` queries. Add `pdf_reader.py` utility that extracts text via `pdfplumber` with Gemini 2.5 Flash vision fallback. Unify all config into `Settings` singleton validated at startup.

**Tech Stack:** PostgreSQL 16, asyncpg, pdfplumber, Pillow, FastAPI lifespan events, pytest + pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-04-13-platform-redesign-design.md` — Phase 1

---

## File Map

### New Files
| File | Responsibility |
|------|---------------|
| `orchestrator/settings.py` | Unified settings from `.env`, validated at startup |
| `orchestrator/services/database_service.py` | Async PostgreSQL client (replaces NotionService + vip_manager) |
| `orchestrator/utils/pdf_reader.py` | PDF text extraction (pdfplumber + vision fallback) |
| `sql/schema.sql` | Complete database schema |
| `scripts/migrate_to_postgres.py` | One-time migration from JSONs to Postgres |
| `tests/test_database_service.py` | DatabaseService unit tests |
| `tests/test_settings.py` | Settings validation tests |
| `tests/test_pdf_reader.py` | PDF reader tests |

### Modified Files
| File | What Changes |
|------|-------------|
| `orchestrator/main.py` | FastAPI lifespan (db pool), replace NotionService init, replace `get_account_by_token` |
| `orchestrator/handlers/email_processor.py` | Replace Notion/vip_manager calls with DatabaseService, add PDF extraction step |
| `orchestrator/services/gmail_service.py` | Add `get_attachment()` method, extract attachment metadata in `_parse_message()` |
| `orchestrator/services/learning_engine.py` | Read feedback from DatabaseService instead of Qdrant corrected emails |
| `orchestrator/services/qdrant_service.py` | Remove `get_learning_counter`/`update_learning_counter` methods |
| `docker-compose.yml` | Add postgres container, update orchestrator depends_on, remove config.json volume |
| `Dockerfile` | Remove vip_manager.py copy, remove JSON state file creation, add pdfplumber deps |
| `requirements.txt` | Add asyncpg, pdfplumber; remove notion-client, PyMuPDF |
| `.env.example` | Add DATABASE_URL, POSTGRES_PASSWORD, LLM_VISION_MODEL, FUNNEL_BASE_URL, TELEGRAM_ALERT_USER_ID; remove Notion vars |
| `.gitignore` | Add pgdata/, playbooks/ |

### Deleted Files
| File | Reason |
|------|--------|
| `orchestrator/services/notion_service.py` | Replaced by DatabaseService |
| `orchestrator/services/company_service.py` | Migrated to DatabaseService (clients/domain_rules in Phase 4) |
| `orchestrator/services/gog_service.py` | Residual file, already replaced by gmail_service |
| `vip_manager.py` | Replaced by DatabaseService |
| `config.json` | Replaced by .env + Postgres |

---

## Task 1: SQL Schema

**Files:**
- Create: `sql/schema.sql`

- [ ] **Step 1: Write the schema file**

```sql
-- sql/schema.sql
-- Email Agent Platform — PostgreSQL Schema

CREATE TABLE accounts (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    hook_token_env VARCHAR(100) NOT NULL,
    oauth_token_path VARCHAR(255),
    telegram_topic_id BIGINT,
    learning_counter INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE vip_list (
    id SERIAL PRIMARY KEY,
    account_id INT REFERENCES accounts(id) ON DELETE CASCADE,
    sender_email VARCHAR(255) NOT NULL,
    sender_name VARCHAR(255),
    min_urgency VARCHAR(20) DEFAULT 'high',
    added_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(account_id, sender_email)
);

CREATE TABLE blacklist (
    id SERIAL PRIMARY KEY,
    account_id INT REFERENCES accounts(id) ON DELETE CASCADE,
    sender_email VARCHAR(255) NOT NULL,
    reason VARCHAR(255),
    added_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(account_id, sender_email)
);

CREATE TABLE feedback (
    id SERIAL PRIMARY KEY,
    account_id INT REFERENCES accounts(id) ON DELETE CASCADE,
    email_id VARCHAR(100) NOT NULL,
    sender VARCHAR(255),
    original_urgency VARCHAR(20),
    corrected_urgency VARCHAR(20),
    keywords TEXT[],
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE decisions (
    id SERIAL PRIMARY KEY,
    account_id INT REFERENCES accounts(id) ON DELETE CASCADE,
    email_id VARCHAR(100) NOT NULL,
    subject TEXT,
    sender VARCHAR(255),
    classification VARCHAR(50),
    priority VARCHAR(20),
    category VARCHAR(50),
    action VARCHAR(50),
    summary TEXT,
    reasoning_tokens INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE tasks (
    id SERIAL PRIMARY KEY,
    account_id INT REFERENCES accounts(id) ON DELETE CASCADE,
    email_id VARCHAR(100),
    title TEXT NOT NULL,
    priority VARCHAR(20) DEFAULT 'Média',
    status VARCHAR(20) DEFAULT 'Pendente',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE history_ids (
    account_id INT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    history_id VARCHAR(50) NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Phase 2 tables (forward-planned — included here so the schema file is
-- complete and Phase 2 only needs ALTER/migration, not a second init script.
-- If Phase 2 design changes, update this file before deploying Phase 2.)

CREATE TABLE metrics (
    id SERIAL PRIMARY KEY,
    request_id VARCHAR(8),
    account_id INT REFERENCES accounts(id),
    event VARCHAR(50) NOT NULL,
    service VARCHAR(30),
    latency_ms INT,
    tokens_used INT,
    success BOOLEAN DEFAULT true,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_metrics_created ON metrics(created_at);
CREATE INDEX idx_metrics_event ON metrics(event);

CREATE TABLE failed_jobs (
    id SERIAL PRIMARY KEY,
    account_id INT REFERENCES accounts(id),
    job_type VARCHAR(50) NOT NULL,
    payload JSONB NOT NULL,
    attempts INT DEFAULT 0,
    max_attempts INT DEFAULT 5,
    last_error TEXT,
    next_retry_at TIMESTAMPTZ,
    status VARCHAR(20) DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_failed_jobs_status ON failed_jobs(status, next_retry_at);
```

- [ ] **Step 2: Commit**

```bash
git add sql/schema.sql
git commit -m "feat: add PostgreSQL schema for platform migration"
```

---

## Task 2: Settings Object

**Files:**
- Create: `orchestrator/settings.py`
- Create: `tests/test_settings.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_settings.py
import os
import pytest
from unittest.mock import patch


def _minimal_env():
    """Minimal valid env vars for Settings."""
    return {
        "OPENROUTER_API_KEY": "sk-or-test",
        "OPENAI_API_KEY": "sk-test",
        "TELEGRAM_BOT_TOKEN": "123:ABC",
        "TELEGRAM_CHAT_ID": "-100123",
        "TELEGRAM_ALLOWED_USER_IDS": "111,222",
        "TELEGRAM_WEBHOOK_SECRET": "secret",
        "TELEGRAM_ALERT_USER_ID": "111",
        "DATABASE_URL": "postgresql://user:pass@localhost:5432/test",
        "FUNNEL_BASE_URL": "https://machine.ts.net",
        "GMAIL_ACCOUNT_1": "test@gmail.com",
        "GMAIL_HOOK_TOKEN_1": "token123",
    }


class TestSettings:
    def test_loads_valid_env(self):
        with patch.dict(os.environ, _minimal_env(), clear=False):
            from orchestrator.settings import Settings
            s = Settings()
            assert s.openrouter_api_key == "sk-or-test"
            assert s.telegram_allowed_user_ids == {111, 222}
            assert s.gmail_accounts == {"test@gmail.com": "token123"}
            assert s.llm_model == "z-ai/glm-5-turbo"  # default

    def test_fails_on_missing_required(self):
        env = _minimal_env()
        del env["DATABASE_URL"]
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="DATABASE_URL"):
                from orchestrator.settings import Settings
                Settings()

    def test_parses_multiple_gmail_accounts(self):
        env = _minimal_env()
        env["GMAIL_ACCOUNT_2"] = "biz@company.com"
        env["GMAIL_HOOK_TOKEN_2"] = "token456"
        with patch.dict(os.environ, env, clear=False):
            from orchestrator.settings import Settings
            s = Settings()
            assert len(s.gmail_accounts) == 2
            assert s.gmail_accounts["biz@company.com"] == "token456"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_settings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator.settings'`

- [ ] **Step 3: Write the Settings implementation**

```python
# orchestrator/settings.py
"""Unified settings loaded from .env and validated at startup."""

import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_REQUIRED = [
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_ALLOWED_USER_IDS",
    "TELEGRAM_WEBHOOK_SECRET",
    "TELEGRAM_ALERT_USER_ID",
    "DATABASE_URL",
    "FUNNEL_BASE_URL",
]


class Settings:
    """Loads and validates all configuration from environment variables."""

    def __init__(self):
        # Validate required vars
        missing = [v for v in _REQUIRED if not os.getenv(v, "").strip()]
        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing)}"
            )

        # LLM
        self.openrouter_api_key: str = os.environ["OPENROUTER_API_KEY"]
        self.openai_api_key: str = os.environ["OPENAI_API_KEY"]
        self.llm_model: str = os.getenv("LLM_MODEL", "z-ai/glm-5-turbo")
        self.llm_vision_model: str = os.getenv("LLM_VISION_MODEL", "google/gemini-2.5-flash")
        self.embedding_model: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

        # Telegram
        self.telegram_bot_token: str = os.environ["TELEGRAM_BOT_TOKEN"]
        self.telegram_chat_id: str = os.environ["TELEGRAM_CHAT_ID"]
        self.telegram_webhook_secret: str = os.environ["TELEGRAM_WEBHOOK_SECRET"]
        self.telegram_alert_user_id: int = int(os.environ["TELEGRAM_ALERT_USER_ID"])
        self.telegram_allowed_user_ids: set = {
            int(uid.strip())
            for uid in os.environ["TELEGRAM_ALLOWED_USER_IDS"].split(",")
            if uid.strip()
        }

        # Database
        self.database_url: str = os.environ["DATABASE_URL"]

        # Base dir
        self.base_dir: str = os.getenv("EMAIL_AGENT_BASE_DIR", ".")

        # Qdrant
        self.qdrant_host: str = os.getenv("QDRANT_HOST", "localhost")
        self.qdrant_port: int = int(os.getenv("QDRANT_PORT", "6333"))

        # Tailscale
        self.funnel_base_url: str = os.environ["FUNNEL_BASE_URL"]

        # Gmail accounts: GMAIL_ACCOUNT_N → GMAIL_HOOK_TOKEN_N
        self.gmail_accounts: dict = {}
        for i in range(1, 20):
            account = os.getenv(f"GMAIL_ACCOUNT_{i}", "").strip()
            token = os.getenv(f"GMAIL_HOOK_TOKEN_{i}", "").strip()
            if account and token:
                self.gmail_accounts[account] = token
            else:
                break

        # Learning
        self.learning_interval: int = int(os.getenv("LEARNING_INTERVAL", "50"))

        logger.info(
            "Settings loaded: %d Gmail accounts, model=%s",
            len(self.gmail_accounts),
            self.llm_model,
        )


# Module-level singleton — import as: from orchestrator.settings import settings
settings: Settings = None  # type: ignore


def get_settings() -> Settings:
    """Lazy singleton — created on first call so tests can patch env first."""
    global settings
    if settings is None:
        settings = Settings()
    return settings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_settings.py -v`
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add orchestrator/settings.py tests/test_settings.py
git commit -m "feat: add unified Settings object with validation"
```

---

## Task 3: DatabaseService

**Files:**
- Create: `orchestrator/services/database_service.py`
- Create: `tests/test_database_service.py`

- [ ] **Step 1: Write the failing tests**

Tests use a mock asyncpg pool. Focus on the core methods that replace NotionService and vip_manager.

```python
# tests/test_database_service.py
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def mock_pool():
    pool = AsyncMock()
    conn = AsyncMock()
    # pool.acquire() returns an async context manager
    ctx = AsyncMock()
    ctx.__aenter__.return_value = conn
    ctx.__aexit__.return_value = False
    pool.acquire.return_value = ctx
    return pool, conn


class TestDatabaseService:

    @pytest.mark.asyncio
    async def test_get_account_returns_dict(self, mock_pool):
        pool, conn = mock_pool
        conn.fetchrow.return_value = {
            "id": 1, "email": "test@gmail.com",
            "hook_token_env": "TOKEN", "telegram_topic_id": 123,
            "learning_counter": 0,
        }
        from orchestrator.services.database_service import DatabaseService
        db = DatabaseService(pool)
        result = await db.get_account("test@gmail.com")
        assert result["id"] == 1
        assert result["email"] == "test@gmail.com"

    @pytest.mark.asyncio
    async def test_get_account_returns_none_if_missing(self, mock_pool):
        pool, conn = mock_pool
        conn.fetchrow.return_value = None
        from orchestrator.services.database_service import DatabaseService
        db = DatabaseService(pool)
        result = await db.get_account("missing@gmail.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_is_blacklisted_true(self, mock_pool):
        pool, conn = mock_pool
        conn.fetchval.return_value = True
        from orchestrator.services.database_service import DatabaseService
        db = DatabaseService(pool)
        result = await db.is_blacklisted(1, "spam@domain.com")
        assert result is True

    @pytest.mark.asyncio
    async def test_is_blacklisted_false(self, mock_pool):
        pool, conn = mock_pool
        conn.fetchval.return_value = False
        from orchestrator.services.database_service import DatabaseService
        db = DatabaseService(pool)
        result = await db.is_blacklisted(1, "friend@domain.com")
        assert result is False

    @pytest.mark.asyncio
    async def test_add_vip(self, mock_pool):
        pool, conn = mock_pool
        conn.execute.return_value = "INSERT 0 1"
        from orchestrator.services.database_service import DatabaseService
        db = DatabaseService(pool)
        result = await db.add_vip(1, "vip@domain.com", "VIP Person", "high")
        assert result is True

    @pytest.mark.asyncio
    async def test_log_decision(self, mock_pool):
        pool, conn = mock_pool
        conn.fetchval.return_value = 42
        from orchestrator.services.database_service import DatabaseService
        db = DatabaseService(pool)
        result = await db.log_decision({
            "account_id": 1, "email_id": "abc123",
            "subject": "Test", "from": "sender@test.com",
            "classificacao": "trabalho", "prioridade": "Alta",
            "categoria": "trabalho", "acao": "notificar",
            "resumo": "Test email", "reasoning_tokens": 100,
        })
        assert result == 42

    @pytest.mark.asyncio
    async def test_get_account_config(self, mock_pool):
        pool, conn = mock_pool
        # Mock account
        conn.fetchrow.return_value = {
            "id": 1, "email": "t@g.com", "hook_token_env": "T",
            "telegram_topic_id": 5, "learning_counter": 0,
        }
        # Mock VIPs
        conn.fetch.side_effect = [
            [{"sender_email": "vip@test.com"}],  # VIPs
        ]
        from orchestrator.services.database_service import DatabaseService
        db = DatabaseService(pool)
        config = await db.get_account_config("t@g.com")
        assert "vips" in config
        assert config["telegram_topic"] == 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_database_service.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the DatabaseService implementation**

```python
# orchestrator/services/database_service.py
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
        """Log email processing decision.

        Key mapping from email_processor:
          "from" → sender, "classificacao" → classification,
          "prioridade" → priority, "categoria" → category,
          "acao" → action, "resumo" → summary
        """
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_database_service.py -v`
Expected: All PASSED

- [ ] **Step 5: Commit**

```bash
git add orchestrator/services/database_service.py tests/test_database_service.py
git commit -m "feat: add DatabaseService replacing Notion and JSON persistence"
```

---

## Task 4: PDF Reader

**Files:**
- Create: `orchestrator/utils/pdf_reader.py`
- Create: `tests/test_pdf_reader.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pdf_reader.py
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


class TestPdfReader:

    @pytest.mark.asyncio
    async def test_extract_text_from_text_pdf(self):
        """pdfplumber can extract text — should return it directly."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Invoice #123\nTotal: $500"
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)

        with patch("pdfplumber.open", return_value=mock_pdf):
            from orchestrator.utils.pdf_reader import PdfReader
            reader = PdfReader(vision_model="test-model", openrouter_key="key")
            result = await reader.extract(b"fake-pdf-bytes")
            assert "Invoice #123" in result
            assert "Total: $500" in result

    @pytest.mark.asyncio
    async def test_returns_empty_for_no_text(self):
        """pdfplumber returns empty — no vision client configured."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = ""
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)

        with patch("pdfplumber.open", return_value=mock_pdf):
            from orchestrator.utils.pdf_reader import PdfReader
            reader = PdfReader(vision_model="", openrouter_key="")
            result = await reader.extract(b"fake-pdf-bytes")
            assert result == ""

    @pytest.mark.asyncio
    async def test_vision_fallback_calls_openrouter(self):
        """When pdfplumber returns empty and vision is configured, call OpenRouter."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = ""
        mock_img = MagicMock()
        mock_img.original = MagicMock()  # PIL Image mock
        mock_page.to_image.return_value = mock_img
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)

        mock_response = AsyncMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "OCR extracted text"}}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("pdfplumber.open", return_value=mock_pdf), \
             patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client
            # Patch PIL Image.save to write fake PNG bytes
            with patch.object(mock_img.original, "save", side_effect=lambda buf, **kw: buf.write(b"fakepng")):
                from orchestrator.utils.pdf_reader import PdfReader
                reader = PdfReader(vision_model="google/gemini-2.5-flash", openrouter_key="sk-test")
                result = await reader.extract(b"fake-pdf-bytes")
                assert result == "OCR extracted text"
                mock_client.post.assert_called_once()

    def test_page_limit_large_pdf(self):
        """PDFs > 10 pages should select first 5 + last 2."""
        from orchestrator.utils.pdf_reader import PdfReader
        reader = PdfReader(vision_model="m", openrouter_key="k")
        pages = list(range(20))  # 20 pages
        selected = reader._select_pages(pages)
        assert selected == [0, 1, 2, 3, 4, 18, 19]

    def test_page_limit_small_pdf(self):
        """PDFs <= 10 pages should use all."""
        from orchestrator.utils.pdf_reader import PdfReader
        reader = PdfReader(vision_model="m", openrouter_key="k")
        pages = list(range(5))
        selected = reader._select_pages(pages)
        assert selected == [0, 1, 2, 3, 4]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_pdf_reader.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the PdfReader implementation**

```python
# orchestrator/utils/pdf_reader.py
"""PDF text extraction with pdfplumber + Gemini vision fallback."""

import io
import base64
import logging
from typing import List, Any

import pdfplumber
import httpx

logger = logging.getLogger(__name__)

MAX_PAGES_FULL = 10
FIRST_PAGES = 5
LAST_PAGES = 2
MAX_CHARS = 15000


class PdfReader:
    """Extract text from PDF bytes.

    Strategy:
    1. Try pdfplumber for text extraction (free, fast)
    2. If no text found and vision model configured, convert to images
       and send to Gemini 2.5 Flash via OpenRouter for OCR
    """

    def __init__(self, vision_model: str, openrouter_key: str):
        self._vision_model = vision_model
        self._openrouter_key = openrouter_key

    async def extract(self, pdf_bytes: bytes) -> str:
        """Extract text from PDF bytes. Returns empty string on failure."""
        try:
            text = self._extract_with_pdfplumber(pdf_bytes)
            if text.strip():
                return text[:MAX_CHARS]

            # Fallback to vision if configured
            if self._vision_model and self._openrouter_key:
                logger.info("pdfplumber returned no text, falling back to vision OCR")
                return await self._extract_with_vision(pdf_bytes)

            return ""
        except Exception as e:
            logger.error(f"PDF extraction failed: {e}")
            return ""

    def _extract_with_pdfplumber(self, pdf_bytes: bytes) -> str:
        """Extract text using pdfplumber."""
        pages_text = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            selected = self._select_pages(pdf.pages)
            for page in selected:
                text = page.extract_text() or ""
                if text.strip():
                    pages_text.append(text)
        return "\n\n".join(pages_text)

    async def _extract_with_vision(self, pdf_bytes: bytes) -> str:
        """Convert PDF pages to images via pdfplumber/Pillow and send to vision LLM for OCR."""
        try:
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                selected = self._select_pages(pdf.pages)
                images_b64 = []
                for page in selected:
                    img = page.to_image(resolution=150).original  # PIL Image
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    images_b64.append(base64.b64encode(buf.getvalue()).decode())

            if not images_b64:
                return ""

            # Call OpenRouter with vision
            content = [{"type": "text", "text": "Extract all text from these PDF pages. Return only the raw text content, no commentary."}]
            for img in images_b64:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{img}"}
                })

            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._openrouter_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._vision_model,
                        "messages": [{"role": "user", "content": content}],
                        "max_tokens": 4000,
                    },
                )
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"][:MAX_CHARS]

        except Exception as e:
            logger.error(f"Vision OCR failed: {e}")
            return ""

    def _select_pages(self, pages: List[Any]) -> List[Any]:
        """Select pages to process: all if <=10, else first 5 + last 2."""
        if len(pages) <= MAX_PAGES_FULL:
            return pages
        return pages[:FIRST_PAGES] + pages[-LAST_PAGES:]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_pdf_reader.py -v`
Expected: All PASSED

- [ ] **Step 5: Commit**

```bash
git add orchestrator/utils/pdf_reader.py tests/test_pdf_reader.py
git commit -m "feat: add PDF reader with pdfplumber + vision fallback"
```

---

## Task 5: Gmail Attachment Support

**Files:**
- Modify: `orchestrator/services/gmail_service.py`

- [ ] **Step 1: Write failing tests for attachment methods**

```python
# Add to existing tests or create tests/test_gmail_attachments.py
import pytest
from unittest.mock import MagicMock

class TestExtractAttachments:
    def test_extracts_pdf_attachment(self):
        from orchestrator.services.gmail_service import GmailService
        service = GmailService.__new__(GmailService)
        payload = {
            "parts": [
                {"filename": "invoice.pdf", "mimeType": "application/pdf",
                 "body": {"attachmentId": "att123", "size": 5000}},
                {"filename": "", "body": {}},  # no attachment
            ]
        }
        result = service._extract_attachments(payload)
        assert len(result) == 1
        assert result[0]["filename"] == "invoice.pdf"
        assert result[0]["attachmentId"] == "att123"

    def test_extracts_nested_attachments(self):
        from orchestrator.services.gmail_service import GmailService
        service = GmailService.__new__(GmailService)
        payload = {
            "parts": [{
                "filename": "",
                "body": {},
                "parts": [
                    {"filename": "nested.pdf", "mimeType": "application/pdf",
                     "body": {"attachmentId": "att456", "size": 3000}},
                ]
            }]
        }
        result = service._extract_attachments(payload)
        assert len(result) == 1
        assert result[0]["filename"] == "nested.pdf"

    def test_returns_empty_for_no_attachments(self):
        from orchestrator.services.gmail_service import GmailService
        service = GmailService.__new__(GmailService)
        payload = {"parts": [{"filename": "", "body": {}}]}
        result = service._extract_attachments(payload)
        assert result == []
```

- [ ] **Step 2: Add `get_attachment()` method and update `_parse_message()` to extract attachment metadata**

In `gmail_service.py`, add after the `move_to_label` method (~line 386):

```python
async def get_attachment(self, email_id: str, attachment_id: str, account: str) -> Optional[bytes]:
    """Download attachment bytes by ID."""
    service = self._get_service(account)
    if not service:
        return None
    try:
        result = await asyncio.to_thread(
            service.users().messages().attachments().get(
                userId="me", messageId=email_id, id=attachment_id
            ).execute
        )
        data = result.get("data", "")
        return base64.urlsafe_b64decode(data) if data else None
    except Exception as e:
        logger.error(f"Error fetching attachment {attachment_id}: {e}")
        return None
```

In `_parse_message()` (~line 392), update the `result` dict to populate `attachments` and add attachment extraction from `payload["parts"]`:

```python
# After extracting body, before return:
result["attachments"] = self._extract_attachments(payload)
```

Add new method:

```python
def _extract_attachments(self, payload: dict) -> list:
    """Extract attachment metadata from email payload."""
    attachments = []
    parts = payload.get("parts", [])
    for part in parts:
        filename = part.get("filename", "")
        body = part.get("body", {})
        attachment_id = body.get("attachmentId")
        if filename and attachment_id:
            attachments.append({
                "filename": filename,
                "mimeType": part.get("mimeType", ""),
                "size": body.get("size", 0),
                "attachmentId": attachment_id,
            })
        # Check nested parts
        if part.get("parts"):
            for nested in part["parts"]:
                fn = nested.get("filename", "")
                nb = nested.get("body", {})
                aid = nb.get("attachmentId")
                if fn and aid:
                    attachments.append({
                        "filename": fn,
                        "mimeType": nested.get("mimeType", ""),
                        "size": nb.get("size", 0),
                        "attachmentId": aid,
                    })
    return attachments
```

- [ ] **Step 2: Run existing tests to verify nothing broke**

Run: `python -m pytest tests/ -v`
Expected: All existing tests PASS

- [ ] **Step 3: Commit**

```bash
git add orchestrator/services/gmail_service.py
git commit -m "feat: add attachment download and metadata extraction to Gmail service"
```

---

## Task 6: Integrate DatabaseService into Main Pipeline

**Files:**
- Modify: `orchestrator/main.py`
- Modify: `orchestrator/handlers/email_processor.py`

This is the critical integration task. Replace all NotionService and vip_manager usage.

- [ ] **Step 1: Update `main.py` — lifespan, service init, get_account_by_token**

Key changes in `orchestrator/main.py`:

1. Replace service initialization (lines 97-123):
   - Remove: `NotionService`, `CompanyService` imports and instances
   - Remove: `from vip_manager import is_blacklisted` (was in email_processor)
   - Add: `asyncpg` pool creation in FastAPI lifespan
   - Add: `DatabaseService` initialization
   - Add: `Settings` import and usage

2. Replace `get_account_by_token()` (lines 397-417):
   - Instead of reading `config.json`, iterate `settings.gmail_accounts`
   - Use `hmac.compare_digest` (already done via `constant_time_equals`)

3. Update health check (lines 139-151):
   - Replace `notion` check with `postgres` check

```python
# New lifespan in main.py:
from contextlib import asynccontextmanager
import asyncpg
from orchestrator.settings import Settings

settings = Settings()

@asynccontextmanager
async def lifespan(app):
    # Create DB pool
    app.state.db_pool = await asyncpg.create_pool(
        dsn=settings.database_url, min_size=2, max_size=10
    )
    # Initialize services that need the pool
    global db, processor
    db = DatabaseService(app.state.db_pool)
    processor = EmailProcessor(db, qdrant, llm, gmail, telegram, learning)
    yield
    await app.state.db_pool.close()

app = FastAPI(title="Email Agent", version="2.0.0", lifespan=lifespan)
```

```python
# New get_account_by_token:
def get_account_by_token(token: str) -> Optional[str]:
    for email, hook_token in settings.gmail_accounts.items():
        token_value = os.getenv(hook_token, hook_token)
        if constant_time_equals(token_value, token):
            return email
    return None
```

- [ ] **Step 2: Update `email_processor.py` — replace Notion and vip_manager calls**

Key changes in `orchestrator/handlers/email_processor.py`:

1. Constructor: accept `DatabaseService` instead of `NotionService` and `CompanyService`
2. Line 101: Replace `is_blacklisted(from_email, account=account)` with `await self.db.is_blacklisted(account_id, from_email)`
3. Line 120: Replace `await self.notion.get_account_config(account)` with `await self.db.get_account_config(account)`
4. Lines 144-163: Remove CompanyService calls (migrated in Phase 4)
5. Line 217: Replace `await self.notion.log_decision(decision_data)` with `await self.db.log_decision(decision_data)`
6. Line 253-267: Replace Qdrant learning counter with `await self.db.get_learning_counter(account_id)` and `update_learning_counter`
7. After step 2 (parse), add PDF extraction:

```python
# After email["body_clean"] = self.cleaner.clean(...)
# Extract PDF attachments
for attachment in email.get("attachments", []):
    if attachment.get("mimeType") == "application/pdf":
        logger.info(f"[{email_id}] Extracting PDF: {attachment['filename']}")
        pdf_bytes = await self.gmail.get_attachment(
            email_id, attachment["attachmentId"], account
        )
        if pdf_bytes:
            pdf_text = await self.pdf_reader.extract(pdf_bytes)
            if pdf_text:
                email["body_clean"] += f"\n\n--- ANEXO PDF: {attachment['filename']} ---\n{pdf_text}"
```

- [ ] **Step 3: Update health check endpoint**

In `main.py`, update the `/health` endpoint to check Postgres instead of Notion:

```python
@app.get("/health")
async def health():
    checks = {"status": "ok", "qdrant": qdrant.is_connected()}
    try:
        checks["postgres"] = await db.is_connected()
    except Exception:
        checks["postgres"] = False
    if not all(v for k, v in checks.items() if k != "status"):
        checks["status"] = "degraded"
    return checks
```

Remove any `notion` key from the health check response.

- [ ] **Step 4: Update existing tests for DatabaseService interface**

Search for tests that mock `NotionService` or `CompanyService`:

```bash
grep -rn "NotionService\|CompanyService\|vip_manager" tests/ --include="*.py"
```

For each test found:
- Replace `NotionService` mock with `DatabaseService` mock
- Replace `CompanyService` mock — remove it (Phase 4)
- Replace `vip_manager.is_blacklisted` mock with `db.is_blacklisted` mock
- Update `EmailProcessor` constructor to match new signature: `EmailProcessor(db, qdrant, llm, gmail, telegram, learning, pdf_reader)`

- [ ] **Step 5: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add orchestrator/main.py orchestrator/handlers/email_processor.py tests/
git commit -m "feat: integrate DatabaseService into main pipeline, add PDF extraction"
```

---

## Task 7: Update Docker & Dependencies

**Files:**
- Modify: `docker-compose.yml`
- Modify: `Dockerfile`
- Modify: `requirements.txt`
- Modify: `.env.example`
- Modify: `.gitignore`

- [ ] **Step 1: Update `docker-compose.yml`**

Add postgres service, update orchestrator depends_on, remove config.json and JSON state volumes:

```yaml
# Add before qdrant:
postgres:
  image: postgres:16-alpine
  container_name: email-agent-postgres
  environment:
    POSTGRES_DB: emailagent
    POSTGRES_USER: emailagent
    POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
  volumes:
    - ./pgdata:/var/lib/postgresql/data
    - ./sql/schema.sql:/docker-entrypoint-initdb.d/schema.sql:ro
  ports:
    - "127.0.0.1:5432:5432"
  restart: unless-stopped
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U emailagent"]
    interval: 10s
    timeout: 5s
    retries: 5
  deploy:
    resources:
      limits:
        memory: 256M
```

Update orchestrator: remove `config.json:ro` volume, remove JSON state volumes (vip-list, blacklist, feedback, history_ids), add `depends_on: postgres: condition: service_healthy`.

**Do NOT remove `telegram-poller` service** — it stays until Phase 3 replaces it with a webhook. Instead, update `telegram_poller.py` to import `is_blacklisted` from `database_service` instead of `vip_manager` (a thin compatibility shim — Phase 3 removes the poller entirely).

- [ ] **Step 2: Update `requirements.txt`**

Remove: `notion-client`, `PyMuPDF`
Add: `asyncpg>=0.29.0`, `pdfplumber>=0.10.0`
Keep: `Pillow>=10.0.0` (needed by pdfplumber's `to_image()` for vision fallback)

- [ ] **Step 3: Update `.env.example`**

Add new vars, remove Notion vars:

```env
# --- Database ---
DATABASE_URL=postgresql://emailagent:senha@postgres:5432/emailagent
POSTGRES_PASSWORD=senha_segura_aqui

# --- LLM ---
LLM_VISION_MODEL=google/gemini-2.5-flash

# --- Tailscale ---
FUNNEL_BASE_URL=https://sua-maquina.ts.net

# --- Alertas ---
TELEGRAM_ALERT_USER_ID=123456789

# --- Gmail (multiple accounts) ---
GMAIL_ACCOUNT_1=seu@email.com
GMAIL_HOOK_TOKEN_1=token_hex_aqui
# GMAIL_ACCOUNT_2=contato@empresa.com
# GMAIL_HOOK_TOKEN_2=token_hex_empresa
```

Remove: All `NOTION_*` vars, `GOG_HOOK_ACCOUNT`, `GOG_HOOK_TOKEN_PESSOAL`

- [ ] **Step 4: Update `.gitignore`**

Add:

```
pgdata/
playbooks/
```

- [ ] **Step 5: Update `Dockerfile`**

Remove lines copying `telegram_poller.py`, `vip_manager.py`. Remove JSON state file creation. Add `pdfplumber` system deps if needed (none for slim image).

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml Dockerfile requirements.txt .env.example .gitignore
git commit -m "feat: add PostgreSQL to Docker Compose, update dependencies and config"
```

---

## Task 8: Delete Replaced Files

**Files:**
- Delete: `orchestrator/services/notion_service.py`
- Delete: `orchestrator/services/company_service.py`
- Delete: `orchestrator/services/gog_service.py`
- Delete: `vip_manager.py`
- Delete: `config.json` (if tracked)

- [ ] **Step 1: Remove files**

```bash
git rm orchestrator/services/notion_service.py
git rm orchestrator/services/company_service.py
git rm orchestrator/services/gog_service.py
git rm vip_manager.py
git rm -f config.json 2>/dev/null || true
```

- [ ] **Step 2: Fix any remaining imports**

Search for lingering imports of deleted modules:

```bash
grep -rn "notion_service\|company_service\|gog_service\|vip_manager" orchestrator/ tests/ --include="*.py"
```

Fix each occurrence: remove the import or replace with `database_service`.

- [ ] **Step 3: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: All PASS. Tests that imported NotionService or CompanyService need updating — replace mocks with DatabaseService mocks.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor: remove Notion, CompanyService, GOG, and vip_manager — replaced by DatabaseService"
```

---

## Task 9: Migration Script

**Files:**
- Create: `scripts/migrate_to_postgres.py`

- [ ] **Step 1: Write migration script**

```python
#!/usr/bin/env python3
"""Migrate VIP, blacklist, and feedback data from JSON files to PostgreSQL."""

import os
import sys
import json
import asyncio
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from dotenv import load_dotenv
load_dotenv(PROJECT_DIR / ".env")

import asyncpg


async def migrate():
    dsn = os.environ["DATABASE_URL"]
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=3)

    async with pool.acquire() as conn:
        # Create accounts from env
        i = 1
        accounts = {}
        while True:
            email = os.getenv(f"GMAIL_ACCOUNT_{i}", "").strip()
            token_env = os.getenv(f"GMAIL_HOOK_TOKEN_{i}", "").strip()
            if not email:
                break
            row = await conn.fetchrow(
                """INSERT INTO accounts (email, hook_token_env, oauth_token_path)
                   VALUES ($1, $2, $3)
                   ON CONFLICT (email) DO UPDATE SET hook_token_env = $2
                   RETURNING id""",
                email, f"GMAIL_HOOK_TOKEN_{i}", f"credentials/token_{email}.json",
            )
            accounts[email] = row["id"]
            print(f"  Account: {email} → id={row['id']}")
            i += 1

        if not accounts:
            print("ERROR: No GMAIL_ACCOUNT_N found in .env")
            return

        default_account_id = list(accounts.values())[0]

        # Migrate VIP list
        vip_file = PROJECT_DIR / "vip-list.json"
        if vip_file.exists():
            vips = json.loads(vip_file.read_text(encoding="utf-8"))
            count = 0
            for entry in vips:
                acct_email = entry.get("account", "")
                acct_id = accounts.get(acct_email, default_account_id)
                await conn.execute(
                    """INSERT INTO vip_list (account_id, sender_email, sender_name, min_urgency)
                       VALUES ($1, $2, $3, $4)
                       ON CONFLICT DO NOTHING""",
                    acct_id, entry["email"], entry.get("name", ""),
                    entry.get("min_urgency", "high"),
                )
                count += 1
            print(f"  VIPs migrated: {count}")

        # Migrate blacklist
        bl_file = PROJECT_DIR / "blacklist.json"
        if bl_file.exists():
            blacklist = json.loads(bl_file.read_text(encoding="utf-8"))
            count = 0
            for entry in blacklist:
                acct_email = entry.get("account", "")
                acct_id = accounts.get(acct_email, default_account_id)
                await conn.execute(
                    """INSERT INTO blacklist (account_id, sender_email, reason)
                       VALUES ($1, $2, $3)
                       ON CONFLICT DO NOTHING""",
                    acct_id, entry["email"], entry.get("reason", ""),
                )
                count += 1
            print(f"  Blacklist migrated: {count}")

        # Migrate feedback
        fb_file = PROJECT_DIR / "feedback.json"
        if fb_file.exists():
            feedback = json.loads(fb_file.read_text(encoding="utf-8"))
            count = 0
            for entry in feedback:
                acct_email = entry.get("account", "")
                acct_id = accounts.get(acct_email, default_account_id)
                await conn.execute(
                    """INSERT INTO feedback (account_id, email_id, sender, original_urgency, corrected_urgency, keywords)
                       VALUES ($1, $2, $3, $4, $5, $6)""",
                    acct_id,
                    entry.get("email_id", ""),
                    entry.get("from", ""),
                    entry.get("original_urgency", ""),
                    entry.get("corrected_urgency", ""),
                    entry.get("keywords", []),
                )
                count += 1
            print(f"  Feedback migrated: {count}")

        # Migrate history IDs
        hid_file = PROJECT_DIR / "history_ids.json"
        if hid_file.exists():
            history_ids = json.loads(hid_file.read_text(encoding="utf-8"))
            count = 0
            for email_key, hid_value in history_ids.items():
                acct_id = accounts.get(email_key)
                if acct_id and hid_value:
                    await conn.execute(
                        """INSERT INTO history_ids (account_id, history_id)
                           VALUES ($1, $2)
                           ON CONFLICT (account_id)
                           DO UPDATE SET history_id = $2, updated_at = NOW()""",
                        acct_id, str(hid_value),
                    )
                    count += 1
            print(f"  History IDs migrated: {count}")

    await pool.close()
    print("\nMigration complete!")


if __name__ == "__main__":
    print("Migrating to PostgreSQL...")
    asyncio.run(migrate())
```

- [ ] **Step 2: Commit**

```bash
git add scripts/migrate_to_postgres.py
git commit -m "feat: add migration script from JSON files to PostgreSQL"
```

---

## Task 10: End-to-End Smoke Test

- [ ] **Step 1: Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 2: Verify Docker builds**

```bash
docker-compose build orchestrator
```

Expected: Build succeeds without errors.

- [ ] **Step 3: Verify schema loads in Postgres**

```bash
docker-compose up -d postgres
docker-compose exec postgres psql -U emailagent -d emailagent -c "\dt"
```

Expected: All tables listed (accounts, vip_list, blacklist, feedback, decisions, tasks, history_ids, metrics, failed_jobs).

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "chore: Phase 1 complete — PostgreSQL foundation with PDF support"
```
