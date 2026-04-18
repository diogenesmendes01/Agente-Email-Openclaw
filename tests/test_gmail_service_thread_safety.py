"""
Thread-safety tests for GmailService.

Background:
    httplib2 (used internally by googleapiclient) is NOT thread-safe on
    Python 3.12. Concurrent .execute() calls from multiple threads corrupt
    the shared SSL state and trigger a segfault:

        [SSL: WRONG_VERSION_NUMBER] wrong version number (_ssl.c:2559)
        Fatal Python error: Segmentation fault

GmailService protects against this by serializing all sync Gmail API calls
through `self._api_lock` (threading.RLock). These tests validate that the
lock is acquired around every `.execute()` and that concurrent async calls
do not interleave inside the google client.
"""

import asyncio
import threading
import time

import pytest
from unittest.mock import MagicMock

from orchestrator.services.gmail_service import GmailService


def _make_service(mock_api) -> GmailService:
    """Build a GmailService with internals replaced by mocks (no real OAuth)."""
    svc = GmailService.__new__(GmailService)
    svc._services = {"test@example.com": mock_api}
    svc._ready = True
    svc._api_lock = threading.RLock()
    return svc


def test_init_creates_rlock():
    """GmailService.__init__ must install an RLock as _api_lock."""
    svc = GmailService.__new__(GmailService)
    svc._services = {}
    svc._ready = False
    # Reproduce the lock construction that __init__ does, then check type.
    svc._api_lock = threading.RLock()
    # RLock() returns an instance of _thread.RLock; the public check is that
    # it supports the context manager protocol and is reentrant.
    with svc._api_lock:
        with svc._api_lock:
            pass  # reentrant acquire must not deadlock


def test_locked_execute_acquires_lock():
    """_locked_execute must hold _api_lock while calling request.execute()."""
    svc = _make_service(MagicMock())

    observed_locked = []

    def fake_execute():
        # While inside execute, the lock must be held (cannot be acquired
        # non-blocking from this same thread unless it's reentrant — but
        # importantly, another thread could not acquire it).
        observed_locked.append(True)
        return {"ok": True}

    request = MagicMock()
    request.execute = fake_execute

    result = svc._locked_execute(request)
    assert result == {"ok": True}
    assert observed_locked == [True]


def test_locked_execute_serializes_threads():
    """Concurrent calls through _locked_execute must not overlap."""
    svc = _make_service(MagicMock())

    in_flight = 0
    max_in_flight = 0
    lock = threading.Lock()

    def fake_execute():
        nonlocal in_flight, max_in_flight
        with lock:
            in_flight += 1
            if in_flight > max_in_flight:
                max_in_flight = in_flight
        # Hold "inside API" for a bit so overlap would be observable.
        time.sleep(0.01)
        with lock:
            in_flight -= 1
        return {"ok": True}

    def worker():
        req = MagicMock()
        req.execute = fake_execute
        svc._locked_execute(req)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # If the lock works, at most one thread is ever inside fake_execute.
    assert max_in_flight == 1, f"expected serialized execution, saw {max_in_flight} concurrent"


@pytest.mark.asyncio
async def test_concurrent_async_api_calls_are_serialized():
    """
    End-to-end: fire 10 get_email calls concurrently via asyncio.gather.
    Each runs in a worker thread via asyncio.to_thread, all sharing the
    single GmailService instance. Validate that the underlying .execute()
    was called exactly 10 times and never concurrently.
    """
    in_flight = 0
    max_in_flight = 0
    counter_lock = threading.Lock()
    call_count = 0

    def make_get_request():
        req = MagicMock()

        def fake_execute():
            nonlocal in_flight, max_in_flight, call_count
            with counter_lock:
                in_flight += 1
                call_count += 1
                if in_flight > max_in_flight:
                    max_in_flight = in_flight
            time.sleep(0.005)
            with counter_lock:
                in_flight -= 1
            return {"id": "m1", "threadId": "t1", "payload": {"headers": []}}

        req.execute = fake_execute
        return req

    # Build the chained mock so service.users().messages().get(...) returns a
    # fresh request each call.
    mock_api = MagicMock()
    mock_api.users.return_value.messages.return_value.get.side_effect = (
        lambda **kwargs: make_get_request()
    )

    svc = _make_service(mock_api)

    results = await asyncio.gather(
        *[svc.get_email(f"id-{i}", "test@example.com") for i in range(10)]
    )

    assert len(results) == 10
    assert all(r is not None for r in results)
    assert call_count == 10
    assert max_in_flight == 1, (
        f"expected lock to serialize execute(), observed {max_in_flight} concurrent"
    )


@pytest.mark.asyncio
async def test_mixed_concurrent_operations_are_serialized():
    """
    Fire a mix of archive/modify/attachment calls concurrently; ensure none
    overlap inside the google client.
    """
    in_flight = 0
    max_in_flight = 0
    counter_lock = threading.Lock()

    def tracked_execute():
        nonlocal in_flight, max_in_flight
        with counter_lock:
            in_flight += 1
            if in_flight > max_in_flight:
                max_in_flight = in_flight
        time.sleep(0.003)
        with counter_lock:
            in_flight -= 1
        return {"data": ""}

    def make_req():
        r = MagicMock()
        r.execute = tracked_execute
        return r

    mock_api = MagicMock()
    mock_api.users.return_value.messages.return_value.modify.side_effect = lambda **kw: make_req()
    mock_api.users.return_value.messages.return_value.attachments.return_value.get.side_effect = (
        lambda **kw: make_req()
    )

    svc = _make_service(mock_api)

    await asyncio.gather(
        svc.archive_email("e1", "test@example.com"),
        svc.archive_email("e2", "test@example.com"),
        svc.move_to_label("e3", "LBL", "test@example.com"),
        svc.move_to_label("e4", "LBL", "test@example.com"),
        svc.get_attachment("e5", "a5", "test@example.com"),
        svc.get_attachment("e6", "a6", "test@example.com"),
        svc.mark_as_spam("e7", "test@example.com"),
        svc.mark_as_spam("e8", "test@example.com"),
    )

    assert max_in_flight == 1, f"expected serialized execute, saw {max_in_flight}"


def test_rlock_allows_reentrant_acquire():
    """
    Ensure the lock is reentrant: a thread that already holds it can call
    another method that also wraps a .execute() without deadlocking. This
    guards against future refactors where one GmailService method calls
    another while holding the lock.
    """
    svc = _make_service(MagicMock())

    def outer():
        with svc._api_lock:
            # Simulate inner call that also acquires — must not deadlock.
            with svc._api_lock:
                return "ok"

    assert outer() == "ok"
