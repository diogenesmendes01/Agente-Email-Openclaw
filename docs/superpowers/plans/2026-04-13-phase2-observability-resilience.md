# Phase 2: Observability + Resilience — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add structured logging with request IDs, metrics collection to PostgreSQL, DM alerts for failures, retry with tenacity on all external services, and a failed-jobs queue with async worker.

**Architecture:** Add request-ID middleware, metrics context manager, alert service with throttling, tenacity decorators on external calls, and a background worker that retries failed jobs with exponential backoff.

**Tech Stack:** FastAPI middleware, asyncpg (reuse Phase 1 pool), tenacity, contextvars for request ID propagation

**Spec:** `docs/superpowers/specs/2026-04-13-platform-redesign-design.md` — Phase 2 (sections 2.1–2.5)

**Note:** The `metrics` and `failed_jobs` tables already exist in `sql/schema.sql` (forward-planned in Phase 1). No DDL migration needed.

---

## File Map

### New Files
| File | Responsibility |
|------|---------------|
| `orchestrator/middleware/request_id.py` | Generate request ID, inject into logging context |
| `orchestrator/services/metrics_service.py` | Record metrics to Postgres `metrics` table |
| `orchestrator/services/alert_service.py` | Telegram DM alerts with throttling |
| `orchestrator/services/job_queue.py` | Failed jobs queue + async retry worker |
| `tests/test_metrics_service.py` | MetricsService unit tests |
| `tests/test_alert_service.py` | AlertService unit tests |
| `tests/test_job_queue.py` | JobQueue unit tests |
| `tests/test_request_id.py` | Request ID middleware test |
| `tests/test_retry.py` | Tenacity retry behavior test |
| `tests/conftest.py` | Shared `mock_pool` fixture (moved from per-file duplication) |

### Modified Files
| File | What Changes |
|------|-------------|
| `orchestrator/main.py` | Add middleware, initialize MetricsService/AlertService/JobQueue, start worker |
| `orchestrator/services/gmail_service.py` | Add tenacity retry decorators |
| `orchestrator/services/telegram_service.py` | Add tenacity retry decorators |
| `orchestrator/services/qdrant_service.py` | Add tenacity retry decorators |
| `orchestrator/handlers/email_processor.py` | Use MetricsService to record timings, enqueue failed jobs |
| `orchestrator/settings.py` | Add METRICS_RETENTION_DAYS, ALERT_THROTTLE_MINUTES, JOB_MAX_ATTEMPTS |

---

## Task 0: Shared Test Fixture

**Files:**
- Create or update: `tests/conftest.py`

- [ ] **Step 1: Create shared mock_pool fixture**

```python
# tests/conftest.py
import pytest
from unittest.mock import MagicMock, AsyncMock


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
```

- [ ] **Step 2: Commit**

```bash
git add tests/conftest.py
git commit -m "test: add shared mock_pool fixture to conftest.py"
```

---

## Task 1: Request ID Middleware

**Files:**
- Create: `orchestrator/middleware/__init__.py`
- Create: `orchestrator/middleware/request_id.py`
- Create: `tests/test_request_id.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_request_id.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from orchestrator.middleware.request_id import get_request_id, request_id_var


class TestRequestIdMiddleware:
    def test_request_id_var_default_is_dash(self):
        """Without middleware, request_id should be '-'."""
        assert request_id_var.get("-") == "-"

    @pytest.mark.asyncio
    async def test_middleware_sets_request_id(self):
        """Middleware should set a request ID in the context var."""
        from orchestrator.middleware.request_id import RequestIdMiddleware
        from starlette.testclient import TestClient
        from fastapi import FastAPI

        app = FastAPI()
        app.add_middleware(RequestIdMiddleware)

        @app.get("/test")
        async def test_endpoint():
            rid = get_request_id()
            return {"request_id": rid}

        from httpx import AsyncClient, ASGITransport
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/test")
            data = response.json()
            assert len(data["request_id"]) == 8
            assert data["request_id"] != "-"
```

- [ ] **Step 2: Write the implementation**

```python
# orchestrator/middleware/__init__.py
# (empty)

# orchestrator/middleware/request_id.py
"""Request ID middleware — generates unique ID per request and injects into logging."""

import uuid
import logging
from contextvars import ContextVar
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


def get_request_id() -> str:
    return request_id_var.get("-")


class RequestIdFilter(logging.Filter):
    """Injects request_id into log records."""
    def filter(self, record):
        record.request_id = get_request_id()
        return True


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = uuid.uuid4().hex[:8]
        token = request_id_var.set(rid)
        request.state.request_id = rid
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            request_id_var.reset(token)
```

- [ ] **Step 3: Update logging format in `main.py`**

IMPORTANT: Do NOT add a second `logging.basicConfig()` call (it's a no-op after first call).
Instead, update the EXISTING `basicConfig` call at the top of `main.py` to include `request_id`:

```python
from orchestrator.middleware.request_id import RequestIdFilter, RequestIdMiddleware

# Change the existing format string to:
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [req:%(request_id)s] %(name)s - %(levelname)s - %(message)s',
    handlers=log_handlers
)

# Add filter to all handlers so %(request_id)s is always available:
rid_filter = RequestIdFilter()
for handler in logging.root.handlers:
    handler.addFilter(rid_filter)
```

Add middleware to app:
```python
from orchestrator.middleware.request_id import RequestIdMiddleware
app.add_middleware(RequestIdMiddleware)
```

- [ ] **Step 4: Run tests, verify pass**

```bash
python -m pytest tests/test_request_id.py -v
```

- [ ] **Step 5: Commit**

```bash
git add orchestrator/middleware/ tests/test_request_id.py orchestrator/main.py
git commit -m "feat: add request ID middleware with contextvar propagation"
```

---

## Task 2: MetricsService

**Files:**
- Create: `orchestrator/services/metrics_service.py`
- Create: `tests/test_metrics_service.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_metrics_service.py
import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch


## NOTE: Use the shared mock_pool fixture from tests/conftest.py (see Task 0 below)


class TestMetricsService:

    @pytest.mark.asyncio
    async def test_record_metric(self, mock_pool):
        pool, conn = mock_pool
        conn.execute.return_value = None
        from orchestrator.services.metrics_service import MetricsService
        ms = MetricsService(pool)
        await ms.record(
            event="email_processed",
            service="pipeline",
            account_id=1,
            latency_ms=350,
            tokens_used=150,
            success=True,
        )
        conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_track_context_manager(self, mock_pool):
        pool, conn = mock_pool
        conn.execute.return_value = None
        from orchestrator.services.metrics_service import MetricsService
        ms = MetricsService(pool)
        async with ms.track("llm_call", service="llm", account_id=1) as t:
            t.tokens_used = 200
        conn.execute.assert_called_once()
        # Args: (sql, request_id, account_id, event, service, latency_ms, tokens_used, success, error_message)
        args = conn.execute.call_args.args
        assert args[5] > 0       # latency_ms > 0
        assert args[6] == 200    # tokens_used
        assert args[7] is True   # success

    @pytest.mark.asyncio
    async def test_track_records_failure(self, mock_pool):
        pool, conn = mock_pool
        conn.execute.return_value = None
        from orchestrator.services.metrics_service import MetricsService
        ms = MetricsService(pool)
        try:
            async with ms.track("llm_call", service="llm") as t:
                raise ValueError("test error")
        except ValueError:
            pass
        conn.execute.assert_called_once()
        # Args: (sql, request_id, account_id, event, service, latency_ms, tokens_used, success, error_message)
        args = conn.execute.call_args.args
        assert args[7] is False           # success = False
        assert "test error" in args[8]    # error_message

    @pytest.mark.asyncio
    async def test_cleanup_old_metrics(self, mock_pool):
        pool, conn = mock_pool
        conn.execute.return_value = "DELETE 42"
        from orchestrator.services.metrics_service import MetricsService
        ms = MetricsService(pool)
        deleted = await ms.cleanup(retention_days=90)
        conn.execute.assert_called_once()
```

- [ ] **Step 2: Write the implementation**

```python
# orchestrator/services/metrics_service.py
"""Metrics collection service — records events to PostgreSQL metrics table."""

import time
import logging
from contextlib import asynccontextmanager
from typing import Optional
from orchestrator.middleware.request_id import get_request_id

logger = logging.getLogger(__name__)


class _TrackingContext:
    """Mutable context for the track() context manager."""
    def __init__(self):
        self.tokens_used: int = 0
        self.extra: dict = {}


class MetricsService:
    """Records operational metrics to the metrics table."""

    def __init__(self, pool):
        self._pool = pool

    async def record(
        self,
        event: str,
        service: str = "",
        account_id: Optional[int] = None,
        latency_ms: int = 0,
        tokens_used: int = 0,
        success: bool = True,
        error_message: str = "",
    ):
        """Record a single metric event."""
        try:
            request_id = get_request_id()
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO metrics
                       (request_id, account_id, event, service, latency_ms,
                        tokens_used, success, error_message)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
                    request_id, account_id, event, service,
                    latency_ms, tokens_used, success, error_message,
                )
        except Exception as e:
            logger.warning(f"Failed to record metric: {e}")

    @asynccontextmanager
    async def track(self, event: str, service: str = "", account_id: Optional[int] = None):
        """Context manager that times an operation and records it."""
        ctx = _TrackingContext()
        start = time.monotonic()
        success = True
        error_msg = ""
        try:
            yield ctx
        except Exception as e:
            success = False
            error_msg = str(e)
            raise
        finally:
            latency_ms = int((time.monotonic() - start) * 1000)
            await self.record(
                event=event,
                service=service,
                account_id=account_id,
                latency_ms=latency_ms,
                tokens_used=ctx.tokens_used,
                success=success,
                error_message=error_msg,
            )

    async def cleanup(self, retention_days: int = 90) -> str:
        """Delete metrics older than retention_days."""
        async with self._pool.acquire() as conn:
            return await conn.execute(
                "DELETE FROM metrics WHERE created_at < NOW() - $1 * INTERVAL '1 day'",
                retention_days,
            )
```

- [ ] **Step 3: Run tests, verify pass**

```bash
python -m pytest tests/test_metrics_service.py -v
```

- [ ] **Step 4: Commit**

```bash
git add orchestrator/services/metrics_service.py tests/test_metrics_service.py
git commit -m "feat: add MetricsService with track() context manager"
```

---

## Task 3: AlertService

**Files:**
- Create: `orchestrator/services/alert_service.py`
- Create: `tests/test_alert_service.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_alert_service.py
import pytest
import time
from unittest.mock import AsyncMock, patch, MagicMock


class TestAlertService:

    @pytest.mark.asyncio
    async def test_sends_alert_dm(self):
        from orchestrator.services.alert_service import AlertService
        service = AlertService(
            bot_token="123:ABC",
            alert_user_id=999,
            throttle_minutes=15,
        )
        with patch("orchestrator.services.alert_service.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            sent = await service.alert("oauth_expired", "Token OAuth expirado para test@gmail.com")
            assert sent is True
            mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_throttles_duplicate_alerts(self):
        from orchestrator.services.alert_service import AlertService
        service = AlertService(
            bot_token="123:ABC",
            alert_user_id=999,
            throttle_minutes=15,
        )
        # Simulate recent alert
        service._last_sent["oauth_expired"] = time.monotonic()

        with patch("orchestrator.services.alert_service.httpx.AsyncClient") as MockClient:
            sent = await service.alert("oauth_expired", "Token OAuth expirado")
            assert sent is False
            MockClient.assert_not_called()

    @pytest.mark.asyncio
    async def test_different_alert_types_not_throttled(self):
        from orchestrator.services.alert_service import AlertService
        service = AlertService(
            bot_token="123:ABC",
            alert_user_id=999,
            throttle_minutes=15,
        )
        service._last_sent["oauth_expired"] = time.monotonic()

        with patch("orchestrator.services.alert_service.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            sent = await service.alert("service_failure", "Gmail API down 3x")
            assert sent is True
```

- [ ] **Step 2: Write the implementation**

```python
# orchestrator/services/alert_service.py
"""Alert service — sends DM alerts to operator via Telegram with throttling."""

import time
import logging
from typing import Dict

import httpx

logger = logging.getLogger(__name__)

ALERT_EMOJI = {
    "oauth_expired": "\u26a0\ufe0f",
    "service_failure": "\u274c",
    "queue_buildup": "\ud83d\udce8",
    "watch_expiring": "\u23f0",
    "job_dead": "\u2620\ufe0f",
}


class AlertService:
    """Sends DM alerts to operator's Telegram with per-type throttling."""

    def __init__(self, bot_token: str, alert_user_id: int, throttle_minutes: int = 15):
        self._bot_token = bot_token
        self._alert_user_id = alert_user_id
        self._throttle_seconds = throttle_minutes * 60
        self._last_sent: Dict[str, float] = {}

    async def alert(self, alert_type: str, message: str) -> bool:
        """Send an alert DM. Returns True if sent, False if throttled."""
        # Throttle check
        now = time.monotonic()
        last = self._last_sent.get(alert_type, 0)
        if now - last < self._throttle_seconds:
            logger.debug(f"Alert '{alert_type}' throttled (last sent {int(now - last)}s ago)")
            return False

        emoji = ALERT_EMOJI.get(alert_type, "\ud83d\udea8")
        text = f"{emoji} *Alert: {alert_type}*\n\n{message}"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"https://api.telegram.org/bot{self._bot_token}/sendMessage",
                    json={
                        "chat_id": self._alert_user_id,
                        "text": text,
                        "parse_mode": "Markdown",
                    },
                )
            self._last_sent[alert_type] = now
            if resp.status_code == 200:
                logger.info(f"Alert sent: {alert_type}")
                return True
            else:
                logger.warning(f"Alert send failed: {resp.status_code}")
                return False
        except Exception as e:
            logger.error(f"Alert send error: {e}")
            return False
```

- [ ] **Step 3: Run tests, verify pass**

```bash
python -m pytest tests/test_alert_service.py -v
```

- [ ] **Step 4: Commit**

```bash
git add orchestrator/services/alert_service.py tests/test_alert_service.py
git commit -m "feat: add AlertService with Telegram DM and throttling"
```

---

## Task 4: Tenacity Retry Decorators

**Files:**
- Modify: `orchestrator/services/gmail_service.py`
- Modify: `orchestrator/services/telegram_service.py`
- Modify: `orchestrator/services/qdrant_service.py`

- [ ] **Step 1: Define the retry decorator**

Create a shared retry config in each service (or a shared utils). The pattern:

```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

_retry_external = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((TimeoutError, ConnectionError, httpx.ConnectError, httpx.TimeoutException)),
    reraise=True,
)
```

- [ ] **Step 2: Write a test for retry behavior**

```python
# tests/test_retry.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from tenacity import retry, stop_after_attempt, wait_none, retry_if_exception_type


class TestRetryBehavior:
    @pytest.mark.asyncio
    async def test_retries_on_timeout(self):
        """Verify that a tenacity-decorated function retries on TimeoutError."""
        call_count = 0

        @retry(
            stop=stop_after_attempt(3),
            wait=wait_none(),
            retry=retry_if_exception_type(TimeoutError),
            reraise=True,
        )
        async def flaky_call():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise TimeoutError("timeout")
            return "success"

        result = await flaky_call()
        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_does_not_retry_on_value_error(self):
        """Non-retryable exceptions should propagate immediately."""
        call_count = 0

        @retry(
            stop=stop_after_attempt(3),
            wait=wait_none(),
            retry=retry_if_exception_type(TimeoutError),
            reraise=True,
        )
        async def bad_call():
            nonlocal call_count
            call_count += 1
            raise ValueError("bad input")

        with pytest.raises(ValueError):
            await bad_call()
        assert call_count == 1  # no retry
```

- [ ] **Step 3: Add decorators to `gmail_service.py`**

Apply `@_retry_external` to:
- `get_email()`
- `get_history()`
- `move_to_label()`
- `archive_email()`
- `get_attachment()`
- `send_reply()` (if method exists)

Note: Gmail API uses `google.api_core.exceptions` for errors. Add `HttpError` to the retry exceptions.

- [ ] **Step 4: Add decorators to `telegram_service.py`**

Apply `@_retry_external` to:
- `send_email_notification()`
- Any method that calls `httpx.post()` to Telegram API

- [ ] **Step 5: Add decorators to `qdrant_service.py`**

Apply `@_retry_external` to:
- `search_similar()`
- `store_email()`
- `get_sender_profile()`
- `get_learned_rules()`

- [ ] **Step 6: Run all tests to verify no regressions**

```bash
python -m pytest tests/ -v
```

- [ ] **Step 7: Commit**

```bash
git add orchestrator/services/gmail_service.py orchestrator/services/telegram_service.py orchestrator/services/qdrant_service.py tests/test_retry.py
git commit -m "feat: add tenacity retry decorators to all external service calls"
```

---

## Task 5: Failed Jobs Queue

**Files:**
- Create: `orchestrator/services/job_queue.py`
- Create: `tests/test_job_queue.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_job_queue.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


## NOTE: Use the shared mock_pool fixture from tests/conftest.py


class TestJobQueue:

    @pytest.mark.asyncio
    async def test_enqueue_job(self, mock_pool):
        pool, conn = mock_pool
        conn.fetchval.return_value = 1
        from orchestrator.services.job_queue import JobQueue
        jq = JobQueue(pool)
        job_id = await jq.enqueue(
            job_type="process_email",
            payload={"email_id": "abc", "account": "test@gmail.com"},
            account_id=1,
        )
        assert job_id == 1
        conn.fetchval.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_pending_jobs(self, mock_pool):
        pool, conn = mock_pool
        conn.fetch.return_value = [
            {"id": 1, "job_type": "process_email", "payload": '{"email_id": "abc"}',
             "attempts": 0, "max_attempts": 5, "account_id": 1},
        ]
        from orchestrator.services.job_queue import JobQueue
        jq = JobQueue(pool)
        jobs = await jq.get_pending(limit=10)
        assert len(jobs) == 1
        assert jobs[0]["id"] == 1

    @pytest.mark.asyncio
    async def test_mark_completed(self, mock_pool):
        pool, conn = mock_pool
        conn.execute.return_value = None
        from orchestrator.services.job_queue import JobQueue
        jq = JobQueue(pool)
        await jq.mark_completed(1)
        conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_mark_failed_increments_attempts(self, mock_pool):
        pool, conn = mock_pool
        conn.execute.return_value = None
        conn.fetchrow.return_value = {"attempts": 5, "max_attempts": 5}
        from orchestrator.services.job_queue import JobQueue
        jq = JobQueue(pool)
        is_dead = await jq.mark_failed(1, "Connection timeout")
        assert is_dead is True
        # Verify 3 execute calls: 1) update attempts, 2) fetchrow, 3) set status='dead'
        assert conn.execute.call_count == 2  # increment + set dead
        dead_call = conn.execute.call_args_list[-1]
        assert "dead" in dead_call.args[0]  # SQL contains 'dead'

    @pytest.mark.asyncio
    async def test_mark_failed_not_dead_yet(self, mock_pool):
        pool, conn = mock_pool
        conn.execute.return_value = None
        conn.fetchrow.return_value = {"attempts": 2, "max_attempts": 5}
        from orchestrator.services.job_queue import JobQueue
        jq = JobQueue(pool)
        is_dead = await jq.mark_failed(1, "Timeout")
        assert is_dead is False
```

- [ ] **Step 2: Write the implementation**

```python
# orchestrator/services/job_queue.py
"""Failed jobs queue with retry and exponential backoff."""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class JobQueue:
    """Manages failed jobs in PostgreSQL with retry logic."""

    def __init__(self, pool, max_attempts: int = 5):
        self._pool = pool
        self._max_attempts = max_attempts

    async def enqueue(
        self,
        job_type: str,
        payload: Dict[str, Any],
        account_id: Optional[int] = None,
        max_attempts: Optional[int] = None,
    ) -> int:
        """Add a job to the retry queue."""
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                """INSERT INTO failed_jobs
                   (account_id, job_type, payload, max_attempts, next_retry_at, status)
                   VALUES ($1, $2, $3::jsonb, $4, NOW(), 'pending')
                   RETURNING id""",
                account_id,
                job_type,
                json.dumps(payload),
                max_attempts or self._max_attempts,
            )

    async def get_pending(self, limit: int = 10) -> List[Dict]:
        """Get pending jobs that are ready for retry."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM failed_jobs
                   WHERE status = 'pending' AND next_retry_at <= NOW()
                   ORDER BY next_retry_at ASC
                   LIMIT $1""",
                limit,
            )
            return [dict(r) for r in rows]

    async def mark_completed(self, job_id: int):
        """Mark a job as successfully completed."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE failed_jobs SET status = 'completed' WHERE id = $1",
                job_id,
            )

    async def mark_failed(self, job_id: int, error: str) -> bool:
        """Record a failure. Returns True if job is now dead (max attempts reached)."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """UPDATE failed_jobs
                   SET attempts = attempts + 1,
                       last_error = $2,
                       next_retry_at = NOW() + (POWER(2, attempts + 1) || ' minutes')::INTERVAL
                   WHERE id = $1""",
                job_id, error,
            )
            row = await conn.fetchrow(
                "SELECT attempts, max_attempts FROM failed_jobs WHERE id = $1",
                job_id,
            )
            if row and row["attempts"] >= row["max_attempts"]:
                await conn.execute(
                    "UPDATE failed_jobs SET status = 'dead' WHERE id = $1",
                    job_id,
                )
                return True
            return False

    async def get_dead_count(self) -> int:
        """Get count of dead jobs (for alerting)."""
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM failed_jobs WHERE status = 'dead'"
            ) or 0

    async def get_pending_count(self) -> int:
        """Get count of pending jobs (for alerting)."""
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM failed_jobs WHERE status = 'pending'"
            ) or 0
```

- [ ] **Step 3: Run tests, verify pass**

```bash
python -m pytest tests/test_job_queue.py -v
```

- [ ] **Step 4: Commit**

```bash
git add orchestrator/services/job_queue.py tests/test_job_queue.py
git commit -m "feat: add JobQueue for failed jobs with retry and exponential backoff"
```

---

## Task 6: Integrate Everything into Main Pipeline

**Files:**
- Modify: `orchestrator/main.py`
- Modify: `orchestrator/handlers/email_processor.py`
- Modify: `orchestrator/settings.py`

- [ ] **Step 1: Add new settings**

In `orchestrator/settings.py`, add to `__init__`:

```python
# Observability
self.metrics_retention_days: int = int(os.getenv("METRICS_RETENTION_DAYS", "90"))
self.alert_throttle_minutes: int = int(os.getenv("ALERT_THROTTLE_MINUTES", "15"))
self.job_max_attempts: int = int(os.getenv("JOB_MAX_ATTEMPTS", "5"))
```

- [ ] **Step 2: Update `main.py` lifespan to init new services**

In the lifespan function, after creating `db`, add:

```python
from orchestrator.services.metrics_service import MetricsService
from orchestrator.services.alert_service import AlertService
from orchestrator.services.job_queue import JobQueue

metrics = MetricsService(pool)
alerts = AlertService(
    bot_token=_settings.telegram_bot_token,
    alert_user_id=_settings.telegram_alert_user_id,
    throttle_minutes=_settings.alert_throttle_minutes,
)
job_queue = JobQueue(pool, max_attempts=_settings.job_max_attempts)
```

Pass `metrics`, `alerts`, `job_queue` to the `EmailProcessor` constructor.

- [ ] **Step 3: Add background worker for retrying failed jobs**

In the lifespan, start a background task:

```python
import asyncio

async def retry_worker():
    """Background worker that retries failed jobs every 60 seconds."""
    while True:
        try:
            jobs = await job_queue.get_pending(limit=5)
            for job in jobs:
                try:
                    # Re-process based on job type
                    if job["job_type"] == "process_email":
                        payload = json.loads(job["payload"]) if isinstance(job["payload"], str) else job["payload"]
                        await processor.process_email(payload["email_id"], payload["account"])
                    await job_queue.mark_completed(job["id"])
                except Exception as e:
                    is_dead = await job_queue.mark_failed(job["id"], str(e))
                    if is_dead:
                        await alerts.alert("job_dead", f"Job #{job['id']} ({job['job_type']}) died after max attempts: {e}")
        except Exception as e:
            logger.error(f"Retry worker error: {e}")
        await asyncio.sleep(60)

worker_task = asyncio.create_task(retry_worker())
yield
worker_task.cancel()
try:
    await worker_task
except asyncio.CancelledError:
    pass
await pool.close()
```

- [ ] **Step 4: Add metrics tracking in `email_processor.py`**

In `process_email`, wrap the main processing in a metrics track:

```python
async with self.metrics.track("email_processed", service="pipeline", account_id=account_id) as t:
    # ... existing classification, summary, action code ...
    t.tokens_used = total_reasoning_tokens
```

On failure, enqueue the job:

```python
except Exception as e:
    logger.error(f"[{email_id}] Erro no processamento: {e}", exc_info=True)
    result["status"] = "error"
    result["error"] = str(e)
    # Enqueue for retry
    if self.job_queue:
        await self.job_queue.enqueue(
            job_type="process_email",
            payload={"email_id": email_id, "account": account},
            account_id=account_id,
        )
    return result
```

- [ ] **Step 5: Update health check to include queue status**

```python
@app.get("/health")
async def health_check():
    checks = {
        "postgres": False,
        "qdrant": qdrant.is_connected(),
        "llm": llm.is_configured(),
        "gmail": gmail.is_ready(),
    }
    queue_info = {}
    try:
        if db:
            checks["postgres"] = await db.is_connected()
        if job_queue:
            queue_info["pending_jobs"] = await job_queue.get_pending_count()
            queue_info["dead_jobs"] = await job_queue.get_dead_count()
    except Exception:
        pass

    status = "healthy" if all(checks.values()) else "degraded"
    return {
        "status": status,
        "timestamp": datetime.utcnow().isoformat(),
        "services": {k: "connected" if v else "disconnected" for k, v in checks.items()},
        "queue": queue_info,
    }
```

- [ ] **Step 6: Run all tests**

```bash
python -m pytest tests/ -v
```

- [ ] **Step 7: Commit**

```bash
git add orchestrator/main.py orchestrator/handlers/email_processor.py orchestrator/settings.py
git commit -m "feat: integrate metrics, alerts, and job queue into pipeline"
```

---

## Task 7: Metrics Cleanup Cron + Final Smoke Test

- [ ] **Step 1: Add daily metrics cleanup in lifespan**

In the retry_worker or a separate task, add cleanup logic:

```python
async def maintenance_worker():
    """Daily maintenance — cleanup old metrics. Runs once at startup, then every 24h."""
    while True:
        try:
            result = await metrics.cleanup(retention_days=_settings.metrics_retention_days)
            logger.info(f"Metrics cleanup: {result}")
        except Exception as e:
            logger.error(f"Metrics cleanup error: {e}")
        await asyncio.sleep(86400)  # 24 hours
```

- [ ] **Step 2: Update `.env.example` with new vars**

Add:
```
# --- Observability ---
# METRICS_RETENTION_DAYS=90
# ALERT_THROTTLE_MINUTES=15
# JOB_MAX_ATTEMPTS=5
```

- [ ] **Step 3: Run full test suite**

```bash
python -m pytest tests/ -v
```

- [ ] **Step 4: Final commit**

```bash
git add orchestrator/main.py .env.example
git commit -m "chore: Phase 2 complete — observability and resilience"
```
