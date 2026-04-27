"""Resilient async worker loop with backoff, timeout, request_id and metrics."""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


async def run_resilient_worker(
    name: str,
    fn: Callable[[], Awaitable[None]],
    *,
    interval: float,
    iteration_timeout: float,
    max_backoff: float = 300.0,
    backoff_reset_after: int = 3,
    request_id_var: Optional[Any] = None,
    metrics: Optional[Any] = None,
) -> None:
    """Run `fn` em loop com backoff/timeout/request_id/metricas.

    - sleep(interval) entre iterações OK
    - backoff exponencial em erro (1s -> 2s -> ... -> max_backoff)
    - reset do backoff após backoff_reset_after iterações OK consecutivas
    - timeout per-iteration (asyncio.wait_for)
    - request_id novo por iteração injetado em ContextVar (se fornecido)
    - métricas {name, status} se metrics fornecido
    """
    # Initial backoff is 1s; grows exponentially (1s -> 2s -> ... -> max_backoff).
    backoff = 1.0
    consecutive_ok = 0
    while True:
        if request_id_var is not None:
            request_id_var.set(str(uuid.uuid4()))

        try:
            await asyncio.wait_for(fn(), timeout=iteration_timeout)
            consecutive_ok += 1
            if consecutive_ok >= backoff_reset_after:
                backoff = 1.0
            if metrics is not None:
                _record(metrics, name, "ok")
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info(f"Worker {name} cancelled")
            raise
        except asyncio.TimeoutError:
            consecutive_ok = 0
            logger.error(f"Worker {name} iteration timed out after {iteration_timeout}s")
            if metrics is not None:
                _record(metrics, name, "timeout")
            await asyncio.sleep(min(backoff, max_backoff))
            backoff = min(backoff * 2, max_backoff)
        except Exception as e:
            consecutive_ok = 0
            logger.error(f"Worker {name} error (backoff={backoff:.1f}s): {e}", exc_info=True)
            if metrics is not None:
                _record(metrics, name, "error")
            await asyncio.sleep(min(backoff, max_backoff))
            backoff = min(backoff * 2, max_backoff)


def _record(metrics: Any, name: str, status: str) -> None:
    """Best-effort metric increment. Tolerates different metrics interfaces."""
    try:
        # Try common method shapes; the project's metrics service may
        # have different naming. Caller can pass None to skip.
        for method_name in ("inc", "increment", "record_iteration", "count"):
            method = getattr(metrics, method_name, None)
            if callable(method):
                try:
                    method("worker_iteration_total", labels={"name": name, "status": status})
                    return
                except TypeError:
                    method(name, status)
                    return
    except Exception:
        pass  # don't let metric failure break the loop
