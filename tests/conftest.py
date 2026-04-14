"""pytest configuration for async tests"""
import pytest
from unittest.mock import MagicMock, AsyncMock

pytest_plugins = ['pytest_asyncio']


@pytest.fixture
def mock_pool():
    """Shared mock asyncpg pool fixture.

    asyncpg's pool.acquire() returns a sync context manager (not a coroutine),
    so we use MagicMock for pool and ctx. The ctx's __aenter__/__aexit__ are
    AsyncMock to support `async with pool.acquire() as conn:`.
    conn.transaction() returns an async context manager for transaction blocks.
    """
    pool = MagicMock()
    conn = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__.return_value = conn
    ctx.__aexit__.return_value = False
    pool.acquire.return_value = ctx

    # Support `async with conn.transaction():` used by job_queue
    # asyncpg's conn.transaction() is NOT a coroutine — it returns a context manager directly
    tx_ctx = MagicMock()
    tx_ctx.__aenter__ = AsyncMock(return_value=None)
    tx_ctx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx_ctx)

    return pool, conn
