"""Tests for account_prompt_config CRUD and prompt leakage between accounts."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from orchestrator.services.database_service import DatabaseService
from orchestrator.services.llm_service import LLMService


@pytest.fixture
def mock_pool():
    pool = MagicMock()
    conn = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = ctx
    return pool, conn


class TestAccountPromptConfigCRUD:
    @pytest.mark.asyncio
    async def test_get_returns_none_when_missing(self, mock_pool):
        pool, conn = mock_pool
        conn.fetchrow.return_value = None
        db = DatabaseService(pool)
        assert await db.get_account_prompt_config(1) is None

    @pytest.mark.asyncio
    async def test_get_parses_jsonb_dict(self, mock_pool):
        pool, conn = mock_pool
        conn.fetchrow.return_value = {"config": {"tom_adicional": "formal"}}
        db = DatabaseService(pool)
        cfg = await db.get_account_prompt_config(1)
        assert cfg == {"tom_adicional": "formal"}

    @pytest.mark.asyncio
    async def test_get_parses_jsonb_string(self, mock_pool):
        """asyncpg may return JSONB as a str; CRUD must json.loads it."""
        pool, conn = mock_pool
        conn.fetchrow.return_value = {"config": '{"tom_adicional": "formal"}'}
        db = DatabaseService(pool)
        cfg = await db.get_account_prompt_config(1)
        assert cfg == {"tom_adicional": "formal"}

    @pytest.mark.asyncio
    async def test_set_upserts_entire_config(self, mock_pool):
        pool, conn = mock_pool
        db = DatabaseService(pool)
        await db.set_account_prompt_config(1, {"tom_adicional": "x"})
        conn.execute.assert_called_once()
        args = conn.execute.call_args.args
        assert args[1] == 1
        assert json.loads(args[2]) == {"tom_adicional": "x"}

    @pytest.mark.asyncio
    async def test_update_field_sends_jsonb_set(self, mock_pool):
        pool, conn = mock_pool
        db = DatabaseService(pool)
        await db.update_account_prompt_config_field(1, "tom_adicional", "novo")
        args = conn.execute.call_args.args
        assert "jsonb_set" in args[0]
        assert args[1] == 1
        assert args[2] == "tom_adicional"
        assert json.loads(args[3]) == "novo"

    @pytest.mark.asyncio
    async def test_delete_removes_row(self, mock_pool):
        pool, conn = mock_pool
        db = DatabaseService(pool)
        await db.delete_account_prompt_config(1)
        args = conn.execute.call_args.args
        assert "DELETE FROM account_prompt_config" in args[0]
        assert args[1] == 1

    @pytest.mark.asyncio
    async def test_get_invalid_json_returns_none(self, mock_pool):
        pool, conn = mock_pool
        conn.fetchrow.return_value = {"config": "{not valid json"}
        db = DatabaseService(pool)
        assert await db.get_account_prompt_config(1) is None


class TestPromptIsolationBetweenAccounts:
    """Per-account custom config must NOT leak across emails/accounts."""

    @pytest.fixture
    def llm(self):
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}):
            return LLMService()

    def test_account_a_config_does_not_leak_to_account_b(self, llm):
        email = {"from": "x@y.com", "subject": "S", "body": "B"}
        ctx_a = {"account_prompt_config": {"tom_adicional": "AAAA-unico-de-A"}}
        ctx_b = {"account_prompt_config": {"tom_adicional": "BBBB-unico-de-B"}}

        p_a = llm._build_action_prompt(email, {}, {}, {}, ctx_a)
        p_b = llm._build_action_prompt(email, {}, {}, {}, ctx_b)

        assert "AAAA-unico-de-A" in p_a and "BBBB-unico-de-B" not in p_a
        assert "BBBB-unico-de-B" in p_b and "AAAA-unico-de-A" not in p_b

    def test_no_custom_identical_to_empty_dict(self, llm):
        """Empty custom dict should produce the same prompt as None."""
        email = {"from": "x@y.com", "subject": "S", "body": "B"}
        p_none = llm._build_action_prompt(email, {}, {}, {}, None)
        p_empty = llm._build_action_prompt(email, {}, {}, {}, {"account_prompt_config": {}})
        assert p_none == p_empty
