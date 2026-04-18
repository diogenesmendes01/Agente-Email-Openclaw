"""Tests for orchestrator.utils.bg_tasks — fire_and_forget + drain semantics."""
import asyncio
import pytest

from orchestrator.utils.bg_tasks import fire_and_forget, drain, bg_tasks


@pytest.fixture(autouse=True)
def _clear_bg_tasks():
    """Isolate each test from global bg_tasks state."""
    bg_tasks.clear()
    yield
    bg_tasks.clear()


@pytest.mark.asyncio
async def test_drain_waits_for_child_task_spawned_during_drain():
    """Regression: asyncio.wait takes a snapshot — any task spawned AFTER the
    wait call was made (but before the parent finishes) must still be awaited.
    This is why drain() loops over fresh snapshots of bg_tasks."""
    results: list[str] = []

    async def child():
        await asyncio.sleep(0.05)
        results.append("child")

    async def parent():
        await asyncio.sleep(0.02)
        # Spawn a child while drain is already waiting on `parent`.
        fire_and_forget(child())
        results.append("parent")

    fire_and_forget(parent())

    await drain(timeout=2.0)

    # Both parent and child must have completed before drain returned.
    assert results == ["parent", "child"]
    # Cleanup ran (done_callback removes tasks from bg_tasks).
    assert len(bg_tasks) == 0


@pytest.mark.asyncio
async def test_drain_cancels_tasks_that_exceed_budget():
    """Tasks that don't finish within the deadline must be cancelled, not left
    running to crash later when the HTTP client / DB pool is closed."""
    cancelled_flag = {"value": False}

    async def long_task():
        try:
            await asyncio.sleep(10.0)  # way beyond drain budget
        except asyncio.CancelledError:
            cancelled_flag["value"] = True
            raise

    fire_and_forget(long_task())

    await drain(timeout=0.15)

    assert cancelled_flag["value"], "long_task should have been cancelled"
    assert len(bg_tasks) == 0


@pytest.mark.asyncio
async def test_drain_returns_immediately_when_empty():
    """No tasks → drain is a no-op."""
    assert len(bg_tasks) == 0
    # Should not block even with a generous timeout.
    await drain(timeout=5.0)
    assert len(bg_tasks) == 0


@pytest.mark.asyncio
async def test_drain_returns_when_all_tasks_finish_voluntarily():
    """Fast tasks should let drain return quickly (well before timeout)."""
    async def quick():
        await asyncio.sleep(0.01)

    for _ in range(3):
        fire_and_forget(quick())

    loop = asyncio.get_event_loop()
    start = loop.time()
    await drain(timeout=2.0)
    elapsed = loop.time() - start

    # All 3 tasks took ~10ms each; drain should return well under the 2s budget.
    assert elapsed < 0.5, f"drain took {elapsed:.2f}s (expected < 0.5s)"
    assert len(bg_tasks) == 0


@pytest.mark.asyncio
async def test_drain_works_with_raw_set_without_discard_callbacks():
    """drain() must not consume the full budget when given a set whose tasks
    aren't auto-removed via done-callback. Internal difference_update(done) is
    what makes the helper robust for caller-supplied sets."""
    async def quick():
        await asyncio.sleep(0.01)

    # Raw set — no fire_and_forget, no discard callback
    raw_set: set[asyncio.Task] = {asyncio.create_task(quick()) for _ in range(3)}

    loop = asyncio.get_event_loop()
    start = loop.time()
    await drain(timeout=2.0, task_set=raw_set)
    elapsed = loop.time() - start

    # Without difference_update, drain would re-wait on already-finished tasks
    # until the 2s budget expired. With it, should be near-instant.
    assert elapsed < 0.5, f"drain took {elapsed:.2f}s (expected < 0.5s)"
    assert len(raw_set) == 0, f"raw_set still has {len(raw_set)} tasks"
