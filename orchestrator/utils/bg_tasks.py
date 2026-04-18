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
