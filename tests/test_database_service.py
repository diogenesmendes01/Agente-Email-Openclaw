import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def mock_pool():
    pool = MagicMock()
    conn = AsyncMock()
    # pool.acquire() returns an async context manager directly
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
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
    async def test_claim_email(self, mock_pool):
        pool, conn = mock_pool
        conn.fetchrow.return_value = {"id": 42}
        from orchestrator.services.database_service import DatabaseService
        db = DatabaseService(pool)
        result = await db.claim_email(1, "abc123")
        assert result == 42

    @pytest.mark.asyncio
    async def test_update_decision(self, mock_pool):
        pool, conn = mock_pool
        from orchestrator.services.database_service import DatabaseService
        db = DatabaseService(pool)
        await db.update_decision(42, {
            "subject": "Test", "from": "sender@test.com",
            "classificacao": "trabalho", "prioridade": "Alta",
            "categoria": "trabalho", "acao": "notificar",
            "resumo": "Test email", "reasoning_tokens": 100,
        })
        conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_account_config(self, mock_pool):
        pool, conn = mock_pool
        conn.fetchrow.return_value = {
            "id": 1, "email": "t@g.com", "hook_token_env": "T",
            "telegram_topic_id": 5, "learning_counter": 0,
        }
        conn.fetch.side_effect = [
            [{"sender_email": "vip@test.com"}],  # VIPs
        ]
        from orchestrator.services.database_service import DatabaseService
        db = DatabaseService(pool)
        config = await db.get_account_config("t@g.com")
        assert "vips" in config
        assert config["telegram_topic"] == 5


@pytest.mark.asyncio
async def test_create_pending_action(mock_pool):
    pool, conn = mock_pool
    conn.fetchrow.return_value = {"id": 1}
    from orchestrator.services.database_service import DatabaseService
    db = DatabaseService(pool)
    result = await db.create_pending_action(1, "email123", "archive", 99, 100, 200, {"key": "val"})
    assert result == 1
    conn.fetchrow.assert_called_once()

@pytest.mark.asyncio
async def test_get_pending_action(mock_pool):
    pool, conn = mock_pool
    conn.fetchrow.return_value = {"id": 1, "email_id": "e1", "action_type": "archive", "state": "{}"}
    from orchestrator.services.database_service import DatabaseService
    db = DatabaseService(pool)
    result = await db.get_pending_action("e1", "archive")
    assert result["email_id"] == "e1"

@pytest.mark.asyncio
async def test_delete_pending_action(mock_pool):
    pool, conn = mock_pool
    from orchestrator.services.database_service import DatabaseService
    db = DatabaseService(pool)
    await db.delete_pending_action(1)
    conn.execute.assert_called_once()

@pytest.mark.asyncio
async def test_cleanup_expired_actions(mock_pool):
    pool, conn = mock_pool
    conn.execute.return_value = "DELETE 3"
    from orchestrator.services.database_service import DatabaseService
    db = DatabaseService(pool)
    count = await db.cleanup_expired_actions()
    assert count == 3

@pytest.mark.asyncio
async def test_upsert_company_profile(mock_pool):
    pool, conn = mock_pool
    conn.fetchrow.return_value = {"id": 1}
    from orchestrator.services.database_service import DatabaseService
    db = DatabaseService(pool)
    result = await db.upsert_company_profile(1, "CodeWave", "12.345.678/0001-90", "formal")
    assert result == 1

@pytest.mark.asyncio
async def test_get_company_profile(mock_pool):
    pool, conn = mock_pool
    conn.fetchrow.return_value = {"id": 1, "company_name": "CodeWave", "account_id": 1}
    from orchestrator.services.database_service import DatabaseService
    db = DatabaseService(pool)
    result = await db.get_company_profile(1)
    assert result["company_name"] == "CodeWave"

@pytest.mark.asyncio
async def test_get_playbooks(mock_pool):
    pool, conn = mock_pool
    conn.fetch.return_value = [
        {"id": 1, "trigger_description": "boleto", "response_template": "Segue boleto..."},
    ]
    from orchestrator.services.database_service import DatabaseService
    db = DatabaseService(pool)
    result = await db.get_playbooks(1)
    assert len(result) == 1

@pytest.mark.asyncio
async def test_create_playbook(mock_pool):
    pool, conn = mock_pool
    conn.fetchrow.return_value = {"id": 1}
    from orchestrator.services.database_service import DatabaseService
    db = DatabaseService(pool)
    result = await db.create_playbook(1, "dúvida boleto", "Prezado, segue...")
    assert result == 1
