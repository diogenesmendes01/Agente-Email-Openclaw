"""Shared background task helper with anti-GC strong-ref set.

Webhook handlers and inner fire-and-forget tasks (like Telegram answerCallbackQuery)
both need their tasks tracked, logged on exception, and drained at shutdown.
This module centralizes that bookkeeping so the same pattern is applied everywhere.
"""
import asyncio
import logging

logger = logging.getLogger(__name__)

# Strong references to in-flight tasks. Prevents CPython's GC from collecting
# an asyncio.Task whose caller discards the return value before the task runs.
bg_tasks: set[asyncio.Task] = set()


def _log_task_result(task: asyncio.Task) -> None:
    """Log exceptions from fire-and-forget tasks; never re-raise."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.error(f"Background task failed: {exc}", exc_info=exc)


def fire_and_forget(coro) -> asyncio.Task:
    """Schedule a coroutine as a background task with proper lifecycle.

    The returned task is held in a module-level set until completion, so
    callers that discard the reference won't lose it to GC. Exceptions
    are logged via `_log_task_result`; the set is cleaned up automatically.
    """
    task = asyncio.create_task(coro)
    bg_tasks.add(task)
    task.add_done_callback(_log_task_result)
    task.add_done_callback(bg_tasks.discard)
    return task


async def drain(timeout: float, task_set: set | None = None) -> None:
    """Wait for background tasks to finish, cancelling any that exceed the budget.

    Loops over fresh snapshots of `task_set` (default: module-level `bg_tasks`)
    so tasks spawned mid-drain (e.g. a handler dispatching another
    `fire_and_forget(...)` while being awaited) still get waited on, as long as
    the total timeout budget allows. Anything still in the set after the
    deadline is cancelled and gathered with `return_exceptions=True`.

    Finished tasks are removed from `task_set` on each iteration. Works both
    with the module-level `bg_tasks` (which auto-discards via done-callback)
    and with caller-supplied sets that have no such callback.
    """
    target = task_set if task_set is not None else bg_tasks
    if not target:
        return

    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while target:
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        snapshot = set(target)
        logger.info(
            f"Waiting for {len(snapshot)} bg task(s) ({remaining:.1f}s left)..."
        )
        try:
            done, _pending = await asyncio.wait(snapshot, timeout=remaining)
        except Exception as e:
            logger.warning(f"Error waiting for bg tasks: {e}")
            break
        # Remove finished tasks from the target. Idempotent with fire_and_forget's
        # discard callback, but also correct for caller-supplied sets.
        target.difference_update(done)
        # No tasks completed in this window → wait timed out. Break to avoid
        # spinning on the same still-running set.
        if not done:
            break

    if target:
        pending = set(target)
        logger.warning(
            f"Cancelling {len(pending)} bg task(s) after drain timeout"
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
