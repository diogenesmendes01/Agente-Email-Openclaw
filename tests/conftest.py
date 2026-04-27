"""pytest configuration for async tests"""
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock

pytest_plugins = ['pytest_asyncio']


@pytest.fixture(autouse=True)
def _mock_get_settings(monkeypatch):
    """Stub `get_settings()` so modules that import it at top-level can be used
    in tests without requiring the full set of production env vars.

    Tests that need a specific setting can override this by calling
    `monkeypatch.setattr(...)` themselves.
    """
    fake = SimpleNamespace(no_reply_auto_archive=False)
    monkeypatch.setattr(
        "orchestrator.settings.get_settings",
        lambda: fake,
        raising=False,
    )
    monkeypatch.setattr(
        "orchestrator.handlers.email_processor.get_settings",
        lambda: fake,
        raising=False,
    )


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
