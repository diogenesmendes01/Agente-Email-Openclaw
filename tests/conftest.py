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
    """
    pool = MagicMock()
    conn = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__.return_value = conn
    ctx.__aexit__.return_value = False
    pool.acquire.return_value = ctx
    return pool, conn
