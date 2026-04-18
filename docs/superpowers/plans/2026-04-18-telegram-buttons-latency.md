# Telegram Buttons Latency Optimization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduzir latência clique→feedback de botões inline Telegram de ~2-3s para ~100-300ms sem adicionar dependências novas nem reescrever framework.

**Architecture:** Três mudanças cirúrgicas: (1) singleton `httpx.AsyncClient` com HTTP/2 e pool keep-alive substitui o `async with httpx.AsyncClient()` em 9 funções de `telegram_service.py`; (2) endpoint `/telegram/callback` em `main.py` retorna 200 em <50ms e dispara `handle_callback`/`handle_text_message` via `asyncio.create_task` com set forte anti-GC; (3) `answer_callback` vira fire-and-forget nos 14 pontos de `telegram_callbacks.py`.

**Tech Stack:** Python 3 + FastAPI + httpx (HTTP/2 habilitado) + asyncio. Sem dependências novas além de `httpx[http2]` (adiciona `h2` package).

**Spec reference:** [docs/superpowers/specs/2026-04-18-telegram-buttons-latency-design.md](../specs/2026-04-18-telegram-buttons-latency-design.md)

---

## File Structure (o que vai mudar)

**Modificados:**
- `requirements.txt` — `httpx>=0.26.0` → `httpx[http2]>=0.27.0`
- `orchestrator/services/telegram_service.py` — AsyncClient singleton no `__init__`, remover `async with httpx.AsyncClient()` das 9 funções, adicionar `aclose()`
- `orchestrator/main.py` — helper `_fire_and_forget`, endpoint `/telegram/callback` fire-and-forget, lifespan shutdown aguarda tasks e fecha client
- `orchestrator/handlers/telegram_callbacks.py` — `answer_callback` via `create_task` em vez de `await` (~12 locais)
- `tests/test_telegram_service.py` — mocks adaptados para client singleton
- `tests/test_telegram_callbacks.py` — mocks de `answer_callback` adaptados para fire-and-forget

**Sem alterações:** actions/, database_service, gmail_service, llm_service, prompt_builder, playbooks.

---

## Task 1: Atualizar dependência httpx[http2]

**Files:**
- Modify: `requirements.txt` (line 3)

- [ ] **Step 1: Editar requirements.txt**

Substituir linha 3:
```
httpx>=0.26.0
```
por:
```
httpx[http2]>=0.27.0
```

- [ ] **Step 2: Reinstalar dependências**

Run: `pip install -r requirements.txt`
Expected: instala `h2` (HTTP/2 support) como dependência transitiva. Sem erros.

- [ ] **Step 3: Verificar HTTP/2 disponível**

Run: `python -c "import httpx; c = httpx.AsyncClient(http2=True); print('OK')"`
Expected: `OK` (sem ImportError "Using http2=True requires the h2 package").

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "deps: enable HTTP/2 support in httpx for Telegram client"
```

---

## Task 2: AsyncClient singleton no TelegramService.__init__

**Files:**
- Modify: `orchestrator/services/telegram_service.py:46-54` (método `__init__`)
- Modify: `orchestrator/services/telegram_service.py` (adicionar método `aclose`)
- Test: `tests/test_telegram_service.py` (novo teste)

- [ ] **Step 1: Escrever teste que verifica singleton client**

Adicionar ao topo de `tests/test_telegram_service.py` (após imports):

```python
import httpx as _httpx_real


@pytest.mark.asyncio
async def test_init_creates_singleton_http2_client(tg_service):
    """TelegramService should create one AsyncClient with HTTP/2 and keep-alive pool."""
    assert hasattr(tg_service, "_client")
    assert isinstance(tg_service._client, _httpx_real.AsyncClient)
    # base_url must embed the bot token path
    assert "test-token" in str(tg_service._client.base_url)


@pytest.mark.asyncio
async def test_aclose_closes_client(tg_service):
    """aclose() should close the underlying AsyncClient."""
    await tg_service.aclose()
    assert tg_service._client.is_closed
```

- [ ] **Step 2: Rodar teste (esperado falhar)**

Run: `pytest tests/test_telegram_service.py::test_init_creates_singleton_http2_client tests/test_telegram_service.py::test_aclose_closes_client -v`
Expected: FAIL com `AttributeError: 'TelegramService' object has no attribute '_client'` ou `aclose`.

- [ ] **Step 3: Implementar singleton client no __init__**

Substituir o método `__init__` em `orchestrator/services/telegram_service.py:46-54`:

```python
def __init__(self):
    self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
    self.api_base = f"https://api.telegram.org/bot{self.bot_token}"
    self._configured = bool(self.bot_token)

    # Singleton HTTP/2 client with connection pool. Reused across all
    # API calls — eliminates 200-500ms TCP+TLS handshake per call.
    self._client = httpx.AsyncClient(
        base_url=self.api_base,
        http2=True,
        limits=httpx.Limits(
            max_keepalive_connections=50,
            max_connections=100,
            keepalive_expiry=30.0,
        ),
        timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
    )

    if self._configured:
        logger.info("TelegramService configurado (HTTP/2 + keep-alive)")

async def aclose(self):
    """Close the underlying HTTP client. Call during app shutdown."""
    await self._client.aclose()
```

- [ ] **Step 4: Rodar testes do step 1 (esperado passar)**

Run: `pytest tests/test_telegram_service.py::test_init_creates_singleton_http2_client tests/test_telegram_service.py::test_aclose_closes_client -v`
Expected: PASS (2 testes).

- [ ] **Step 5: Commit**

```bash
git add orchestrator/services/telegram_service.py tests/test_telegram_service.py
git commit -m "feat(telegram): add singleton AsyncClient with HTTP/2 and keep-alive pool"
```

---

## Task 3: Refactor métodos de envio para usar self._client

**Contexto:** 3 métodos atualmente abrem `async with httpx.AsyncClient()` para enviar. Vamos refatorar para usar `self._client`.

**Files:**
- Modify: `orchestrator/services/telegram_service.py` — métodos `_send_message` (linha ~98), `send_confirmation` (linha ~320), `send_text` (linha ~495)
- Modify: `tests/test_telegram_service.py` — substituir `patch("httpx.AsyncClient")` pelo novo padrão

- [ ] **Step 1: Reescrever teste `test_send_text` para o novo padrão**

Substituir `test_send_text` (atualmente em `tests/test_telegram_service.py:133-144`) por:

```python
@pytest.mark.asyncio
async def test_send_text(tg_service):
    """send_text uses the singleton client, returns message_id on success."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"result": {"message_id": 42}}
    tg_service._client.post = AsyncMock(return_value=mock_resp)

    result = await tg_service.send_text(100, "Hello!")

    assert result == 42
    tg_service._client.post.assert_called_once()
    # URL should be relative path (base_url is already set on client)
    call_url = tg_service._client.post.call_args[0][0]
    assert "sendMessage" in call_url
```

- [ ] **Step 2: Rodar teste (esperado falhar)**

Run: `pytest tests/test_telegram_service.py::test_send_text -v`
Expected: FAIL — porque `send_text` ainda usa `async with httpx.AsyncClient()`, não `self._client.post`.

- [ ] **Step 3: Refatorar `send_text` para usar `self._client`**

Em `orchestrator/services/telegram_service.py`, método `send_text` (~linhas 495-515):

```python
async def send_text(self, chat_id: int, text: str, reply_markup: dict = None, thread_id: int = None) -> Optional[int]:
    """Send a text message and return the message_id."""
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if thread_id:
        payload["message_thread_id"] = thread_id
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        response = await self._client.post("/sendMessage", json=payload)
        if response.status_code == 200:
            return response.json().get("result", {}).get("message_id")
    except Exception as e:
        logger.error(f"Error sending text: {e}")
    return None
```

- [ ] **Step 4: Refatorar `_send_message` (linhas ~98-136)**

```python
async def _send_message(
    self, text: str, topic_id: Optional[int] = None,
    reply_markup: Optional[Dict] = None
) -> Optional[int]:
    """Envia uma mensagem individual ao Telegram"""
    payload = {
        "chat_id": self.chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if topic_id:
        payload["message_thread_id"] = topic_id
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        response = await self._client.post("/sendMessage", json=payload)
        if response.status_code == 200:
            data = response.json()
            msg_id = data.get("result", {}).get("message_id")
            logger.info(f"Notificação enviada: message_id={msg_id}")
            return msg_id
        logger.error(f"Erro Telegram: {response.status_code} - {response.text}")
        raise httpx.HTTPStatusError(
            f"Telegram API error: {response.status_code}",
            request=response.request, response=response,
        )
    except (httpx.TimeoutException, httpx.ConnectError):
        raise
    except httpx.HTTPStatusError:
        raise
    except Exception as e:
        logger.error(f"Erro ao enviar: {e}")
        raise
```

- [ ] **Step 5: Refatorar `send_confirmation` (linhas ~320-348)**

```python
async def send_confirmation(
    self,
    chat_id: int,
    thread_id: int,
    text: str,
    buttons: Optional[list] = None
) -> Optional[int]:
    """Envia mensagem de confirmação com botões"""
    payload = {
        "chat_id": chat_id,
        "message_thread_id": thread_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}

    try:
        response = await self._client.post("/sendMessage", json=payload)
        if response.status_code == 200:
            return response.json().get("result", {}).get("message_id")
    except Exception as e:
        logger.error(f"Erro ao enviar confirmação: {e}")
    return None
```

- [ ] **Step 6: Rodar teste refatorado (esperado passar)**

Run: `pytest tests/test_telegram_service.py::test_send_text -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add orchestrator/services/telegram_service.py tests/test_telegram_service.py
git commit -m "refactor(telegram): use singleton client in send methods"
```

---

## Task 4: Refactor métodos de edição para usar self._client

**Files:**
- Modify: `orchestrator/services/telegram_service.py` — `edit_message` (linha ~350), `edit_reply_markup` (linha ~465), `disable_buttons` (linha ~517)
- Modify: `tests/test_telegram_service.py` — `test_edit_reply_markup`

- [ ] **Step 1: Reescrever `test_edit_reply_markup` para o novo padrão**

Substituir em `tests/test_telegram_service.py:33-44`:

```python
@pytest.mark.asyncio
async def test_edit_reply_markup(tg_service):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    tg_service._client.post = AsyncMock(return_value=mock_resp)

    keyboard = {"inline_keyboard": [[{"text": "OK", "callback_data": "ok"}]]}
    result = await tg_service.edit_reply_markup(123, 456, keyboard)

    assert result is True
    tg_service._client.post.assert_called_once()
    call_url = tg_service._client.post.call_args[0][0]
    assert "editMessageReplyMarkup" in call_url
```

- [ ] **Step 2: Rodar teste (esperado falhar)**

Run: `pytest tests/test_telegram_service.py::test_edit_reply_markup -v`
Expected: FAIL.

- [ ] **Step 3: Refatorar `edit_message` (linhas ~350-401)**

```python
async def edit_message(
    self,
    message_id: int,
    text: str,
    chat_id: Optional[str] = None,
    reply_markup: Optional[Dict] = None
) -> bool:
    """Edita texto de uma mensagem existente."""
    if not self._configured:
        logger.warning("Telegram não configurado")
        return False

    chat_id = chat_id or self.chat_id
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        response = await self._client.post("/editMessageText", json=payload)
        if response.status_code == 200:
            logger.info(f"Mensagem {message_id} editada com sucesso")
            return True
        logger.error(f"Erro ao editar mensagem: {response.status_code} - {response.text}")
        return False
    except Exception as e:
        logger.error(f"Exceção ao editar mensagem: {e}")
        return False
```

- [ ] **Step 4: Refatorar `edit_reply_markup` (linhas ~465-480)**

```python
async def edit_reply_markup(self, chat_id: int, message_id: int, reply_markup: dict) -> bool:
    """Edit only the inline keyboard of a message."""
    try:
        response = await self._client.post(
            "/editMessageReplyMarkup",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "reply_markup": reply_markup,
            },
        )
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Error editing reply markup: {e}")
        return False
```

- [ ] **Step 5: Refatorar `disable_buttons` (linhas ~517-558)**

```python
async def disable_buttons(
    self,
    message_id: int,
    chat_id: Optional[str] = None
) -> bool:
    """Remove botões inline de uma mensagem."""
    if not self._configured:
        logger.warning("Telegram não configurado")
        return False

    chat_id = chat_id or self.chat_id
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": {"inline_keyboard": []},
    }
    try:
        response = await self._client.post("/editMessageReplyMarkup", json=payload)
        if response.status_code == 200:
            logger.info(f"Botões removidos da mensagem {message_id}")
            return True
        logger.error(f"Erro ao remover botões: {response.status_code} - {response.text}")
        return False
    except Exception as e:
        logger.error(f"Exceção ao remover botões: {e}")
        return False
```

- [ ] **Step 6: Rodar teste refatorado (esperado passar)**

Run: `pytest tests/test_telegram_service.py::test_edit_reply_markup -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add orchestrator/services/telegram_service.py tests/test_telegram_service.py
git commit -m "refactor(telegram): use singleton client in edit methods"
```

---

## Task 5: Refactor métodos restantes (answer_callback, delete_message, set_webhook)

**Files:**
- Modify: `orchestrator/services/telegram_service.py` — `answer_callback` (linha ~452), `delete_message` (linha ~482), `set_webhook` (linha ~431)
- Modify: `tests/test_telegram_service.py` — `test_answer_callback`, `test_delete_message`, `test_set_webhook`

- [ ] **Step 1: Reescrever os 3 testes para o novo padrão**

Substituir em `tests/test_telegram_service.py`:

```python
@pytest.mark.asyncio
async def test_answer_callback(tg_service):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    tg_service._client.post = AsyncMock(return_value=mock_resp)

    result = await tg_service.answer_callback("cb123", "Done!")

    assert result is True
    tg_service._client.post.assert_called_once()
    call_url = tg_service._client.post.call_args[0][0]
    assert "answerCallbackQuery" in call_url


@pytest.mark.asyncio
async def test_delete_message(tg_service):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    tg_service._client.post = AsyncMock(return_value=mock_resp)

    result = await tg_service.delete_message(123, 456)

    assert result is True


@pytest.mark.asyncio
async def test_set_webhook(tg_service):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True}
    tg_service._client.post = AsyncMock(return_value=mock_resp)

    result = await tg_service.set_webhook("https://example.com/telegram/callback", "secret123")

    assert result is True
```

- [ ] **Step 2: Rodar testes (esperado falhar)**

Run: `pytest tests/test_telegram_service.py::test_answer_callback tests/test_telegram_service.py::test_delete_message tests/test_telegram_service.py::test_set_webhook -v`
Expected: FAIL (3 testes).

- [ ] **Step 3: Refatorar `answer_callback` (linhas ~452-463)**

```python
async def answer_callback(self, callback_id: str, text: str) -> bool:
    """Answer a callback query (acknowledge button press)."""
    try:
        response = await self._client.post(
            "/answerCallbackQuery",
            json={"callback_query_id": callback_id, "text": text},
        )
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Error answering callback: {e}")
        return False
```

- [ ] **Step 4: Refatorar `delete_message` (linhas ~482-493)**

```python
async def delete_message(self, chat_id: int, message_id: int) -> bool:
    """Delete a message."""
    try:
        response = await self._client.post(
            "/deleteMessage",
            json={"chat_id": chat_id, "message_id": message_id},
        )
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Error deleting message: {e}")
        return False
```

- [ ] **Step 5: Refatorar `set_webhook` (linhas ~431-450)**

```python
@_retry_external
async def set_webhook(self, url: str, secret_token: str) -> bool:
    """Register webhook URL with Telegram."""
    response = await self._client.post(
        "/setWebhook",
        json={
            "url": url,
            "secret_token": secret_token,
            "allowed_updates": ["callback_query", "message"],
        },
    )
    if response.status_code == 200 and response.json().get("ok"):
        logger.info(f"Webhook registered: {url}")
        return True
    logger.error(f"Webhook registration failed: {response.text}")
    raise httpx.HTTPStatusError(
        f"Webhook registration failed: {response.status_code}",
        request=response.request, response=response,
    )
```

- [ ] **Step 6: Rodar testes (esperado passar)**

Run: `pytest tests/test_telegram_service.py -v`
Expected: TODOS os testes de `test_telegram_service.py` passam.

- [ ] **Step 7: Verificar que o código não contém mais `async with httpx.AsyncClient`**

Run: `grep -n "async with httpx.AsyncClient" orchestrator/services/telegram_service.py`
Expected: zero linhas. Se aparecer algo, corrigir antes de commit.

- [ ] **Step 8: Commit**

```bash
git add orchestrator/services/telegram_service.py tests/test_telegram_service.py
git commit -m "refactor(telegram): use singleton client in callback/admin methods"
```

---

## Task 6: Fire-and-forget helper em main.py

**Files:**
- Modify: `orchestrator/main.py` — adicionar helper `_fire_and_forget` e set `_bg_tasks`

- [ ] **Step 1: Adicionar helper antes do endpoint `/telegram/callback`**

Em `orchestrator/main.py`, adicionar logo antes da linha 357 (`@app.post("/telegram/callback")`):

```python
# Background tasks spawned from webhooks. Strong ref prevents GC-before-finish
# (known CPython bug with unreferenced asyncio.Tasks).
_bg_tasks: set = set()


def _log_task_result(task):
    """Log exceptions from fire-and-forget tasks; never re-raise."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.error(f"Background webhook task failed: {exc}", exc_info=exc)


def _fire_and_forget(coro):
    """Schedule coroutine in background; webhook returns 200 immediately."""
    import asyncio
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_log_task_result)
    task.add_done_callback(_bg_tasks.discard)
    return task
```

- [ ] **Step 2: Verificar que o helper importa corretamente**

Run: `python -c "from orchestrator.main import _fire_and_forget, _bg_tasks; print('ok')"`
Expected: `ok` (sem erros de import).

- [ ] **Step 3: Commit**

```bash
git add orchestrator/main.py
git commit -m "feat(webhook): add fire-and-forget helper with anti-GC task set"
```

---

## Task 7: Endpoint /telegram/callback fire-and-forget

**Files:**
- Modify: `orchestrator/main.py:357-397` — refatorar endpoint para usar `_fire_and_forget`

- [ ] **Step 1: Escrever teste verificando que webhook retorna 200 rápido**

Criar arquivo novo `tests/test_webhook_fire_and_forget.py`:

```python
"""Tests for fire-and-forget behavior of /telegram/callback endpoint."""
import asyncio
import json
import time
import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport


@pytest.mark.asyncio
async def test_callback_webhook_returns_200_immediately():
    """Webhook must return 200 in <200ms even if handler is slow."""
    from orchestrator.main import app

    async def slow_handler(*args, **kwargs):
        await asyncio.sleep(2.0)  # simulate slow Gmail/LLM

    body = {
        "callback_query": {
            "id": "cb_test",
            "data": "archive:em_1:user@t.com",
            "from": {"id": 42},
            "message": {"message_id": 1, "chat": {"id": 1}, "text": "x"},
        }
    }

    with patch("orchestrator.main.handle_callback", slow_handler), \
         patch.dict("os.environ", {"TELEGRAM_WEBHOOK_SECRET": ""}, clear=False):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            start = time.perf_counter()
            resp = await ac.post("/telegram/callback", json=body)
            elapsed = time.perf_counter() - start

    assert resp.status_code == 200
    assert elapsed < 0.5, f"Webhook blocked {elapsed:.2f}s (expected <0.5s)"
```

- [ ] **Step 2: Rodar teste (esperado falhar)**

Run: `pytest tests/test_webhook_fire_and_forget.py -v`
Expected: FAIL — atualmente o endpoint faz `await handle_callback()` que demora 2s.

- [ ] **Step 3: Refatorar endpoint `/telegram/callback` (linhas ~357-397)**

Em `orchestrator/main.py`, substituir o corpo do `try` block (linhas 374-392):

```python
    try:
        logger.info(f"Telegram update: {json.dumps(body)[:500]}")

        _settings = get_settings()
        services = {"db": db, "gmail": gmail, "telegram": telegram, "llm": llm,
                     "metrics": metrics, "model_registry": model_registry,
                     "allowed_user_ids": _settings.telegram_allowed_user_ids}

        callback_query = body.get("callback_query")
        if callback_query:
            _fire_and_forget(handle_callback(callback_query, services))
            return JSONResponse(status_code=200, content={"status": "ok"})

        message = body.get("message")
        if message and message.get("text"):
            _fire_and_forget(handle_text_message(message, services))
            return JSONResponse(status_code=200, content={"status": "ok"})

        return JSONResponse(status_code=200, content={"status": "ignored"})

    except Exception as e:
        # Only infrastructure errors (JSON, services dict) reach here.
        # Handler errors go to _log_task_result instead.
        logger.error(f"Telegram callback infra error: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"status": "error"})
```

**Nota:** o bloco `except` ficou enxuto porque erros de DB/Gmail/LLM agora acontecem dentro do handler em background, não no escopo do endpoint. Isso é mudança semântica intencional documentada na seção 7.1 do spec.

- [ ] **Step 4: Rodar teste (esperado passar)**

Run: `pytest tests/test_webhook_fire_and_forget.py -v`
Expected: PASS — elapsed ~<100ms apesar do `slow_handler` levar 2s.

- [ ] **Step 5: Rodar toda a suite telegram para garantir zero regressão**

Run: `pytest tests/test_telegram_callbacks.py tests/test_telegram_commands.py -v`
Expected: TODOS passam. Se algum falhar (ex: teste que dependia de `await` síncrono), investigar em Task 9.

- [ ] **Step 6: Commit**

```bash
git add orchestrator/main.py tests/test_webhook_fire_and_forget.py
git commit -m "feat(webhook): fire-and-forget callback handler, return 200 in <50ms"
```

---

## Task 8: Lifespan shutdown com wait de tasks + aclose do client

**Files:**
- Modify: `orchestrator/main.py:183-200` — adicionar wait de `_bg_tasks` e `telegram.aclose()` no shutdown

- [ ] **Step 1: Modificar a seção "Graceful shutdown" do lifespan**

Em `orchestrator/main.py`, no bloco de shutdown (após `yield`, linhas ~183-200), adicionar ANTES do `await pool.close()`:

```python
    # Graceful shutdown
    retry_task.cancel()
    maint_task.cancel()
    cleanup_task.cancel()
    try:
        await retry_task
    except asyncio.CancelledError:
        pass
    try:
        await maint_task
    except asyncio.CancelledError:
        pass
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass

    # Wait for any in-flight webhook background tasks to complete (5s budget)
    if _bg_tasks:
        logger.info(f"Waiting for {len(_bg_tasks)} webhook tasks to finish...")
        try:
            await asyncio.wait(_bg_tasks, timeout=5.0)
        except Exception as e:
            logger.warning(f"Error waiting for bg tasks: {e}")

    # Close the shared Telegram HTTP client
    try:
        await telegram.aclose()
    except Exception as e:
        logger.warning(f"Error closing telegram client: {e}")

    await pool.close()
    logger.info("Email Agent shutdown — pool closed")
```

- [ ] **Step 2: Smoke test: aclose do cliente funciona sem erros**

```bash
python -c "
import asyncio
from orchestrator.main import telegram
async def t():
    await telegram.aclose()
    print('aclose ok')
asyncio.run(t())
"
```
Expected: `aclose ok` sem erros.

- [ ] **Step 3: Commit**

```bash
git add orchestrator/main.py
git commit -m "feat(lifespan): await webhook bg tasks and close telegram client on shutdown"
```

---

## Task 9: answer_callback paralelo em telegram_callbacks.py

**Files:**
- Modify: `orchestrator/handlers/telegram_callbacks.py` — trocar `await tg.answer_callback(...)` por `asyncio.create_task(tg.answer_callback(...))` em ~12 pontos
- Modify: `tests/test_telegram_callbacks.py` — ajustar asserts que dependem do timing

- [ ] **Step 1: Adicionar `import asyncio` no topo do handler**

Em `orchestrator/handlers/telegram_callbacks.py`, se `import asyncio` não estiver no topo (verificar linhas 1-10), adicionar após os imports existentes.

- [ ] **Step 2: Localizar todos os pontos com grep**

Run: `grep -n "await tg.answer_callback(" orchestrator/handlers/telegram_callbacks.py`
Expected: 14 linhas encontradas. Anotar números de linha.

- [ ] **Step 3: Substituir cada ocorrência**

Para **cada** linha encontrada no step anterior, trocar:
```python
await tg.answer_callback(callback_id, "...")
```
por:
```python
asyncio.create_task(tg.answer_callback(callback_id, "..."))
```

**Inclusive o callback do bloco de auth check (`⛔ Acesso não autorizado`):** converter para `create_task` também. A função retorna `return` logo em seguida, então fire-and-forget é seguro — o `_log_task_result` em `main.py` captura qualquer erro.

- [ ] **Step 4: Verificar substituição com grep**

Run: `grep -n "await tg.answer_callback(" orchestrator/handlers/telegram_callbacks.py`
Expected: zero linhas (todas viraram `create_task`).

Run: `grep -cn "asyncio.create_task(tg.answer_callback(" orchestrator/handlers/telegram_callbacks.py`
Expected: número igual ao que o step 2 achou (14).

- [ ] **Step 5: Ajustar testes em test_telegram_callbacks.py**

O padrão antigo `services["telegram"].answer_callback.assert_called_once()` pode falhar porque `create_task` ainda não teve chance de rodar quando o handler retorna. Ajustar adicionando `await asyncio.sleep(0)` antes dos asserts OU converter os asserts para esperar.

Padrão a aplicar nos testes afetados (procurar todos que fazem `assert_called_once` ou `assert_called_with` em `answer_callback`):

```python
# Antes:
await handle_callback(cb, services)
services["telegram"].answer_callback.assert_called_once()

# Depois:
await handle_callback(cb, services)
await asyncio.sleep(0)  # yield to event loop so create_task can run
services["telegram"].answer_callback.assert_called_once()
```

Adicionar `import asyncio` no topo de `tests/test_telegram_callbacks.py` se ausente.

- [ ] **Step 6: Rodar suite de callbacks**

Run: `pytest tests/test_telegram_callbacks.py -v`
Expected: TODOS passam. Se algum falhar, verificar se precisa do `await asyncio.sleep(0)` (ou valores maiores como `asyncio.sleep(0.01)` para múltiplos `create_task` encadeados).

- [ ] **Step 7: Rodar suite completa de Telegram**

Run: `pytest tests/test_telegram_service.py tests/test_telegram_callbacks.py tests/test_telegram_commands.py tests/test_webhook_fire_and_forget.py -v`
Expected: TODOS passam.

- [ ] **Step 8: Commit**

```bash
git add orchestrator/handlers/telegram_callbacks.py tests/test_telegram_callbacks.py
git commit -m "perf(telegram): fire-and-forget answer_callback to remove 1 RTT from critical path"
```

---

## Task 10: Verificação final e smoke test manual

**Files:** nenhum — só verificação

- [ ] **Step 1: Rodar toda a suite de testes**

Run: `pytest -v`
Expected: TODOS os testes passam. Zero regressões.

- [ ] **Step 2: Verificar que `async with httpx.AsyncClient` não aparece mais em telegram_service**

Run: `grep -n "async with httpx.AsyncClient" orchestrator/services/telegram_service.py`
Expected: zero linhas.

- [ ] **Step 3: Startar o orchestrator em ambiente dev e verificar logs**

Run em terminal separado: `uvicorn orchestrator.main:app --reload --port 8787`
Expected: log `TelegramService configurado (HTTP/2 + keep-alive)` aparece no startup.

- [ ] **Step 4: Smoke test manual — clicar em cada botão**

Em qualquer email notificado no Telegram, clicar nos 8 botões (um por vez, aceitando e testando fluxos):

- [ ] ✅ Arquivar — confirma e completa
- [ ] ⭐ Marcar VIP — confirma e completa
- [ ] 🔇 Silenciar — confirma e completa
- [ ] 🗑️ Spam — confirma e completa
- [ ] ✉️ Enviar rascunho — confirma e completa (verificar que email foi enviado)
- [ ] 📝 Criar tarefa — envia descrição e completa
- [ ] 💬 Responder custom — envia instrução, vê rascunho, confirma, envia
- [ ] 🔄 Reclassificar — escolhe urgência, vê botões restaurados

Para cada: cronometrar "clique → feedback visual de loading na mensagem aparecer". Meta: < 500ms.

- [ ] **Step 5: Verificar ausência de callback duplicados nos logs**

Com smoke test rodando, checar logs:
```bash
grep "Telegram update:" logs/email_agent.log | grep -oP 'callback_query":\{"id":"[^"]+"' | sort | uniq -c | sort -rn | head -5
```
Expected: cada `callback_query.id` aparece **1 vez** (não 2+, que seria retry).

- [ ] **Step 6: Shutdown gracioso**

No terminal do uvicorn, apertar Ctrl+C.
Expected: logs mostram "Waiting for N webhook tasks to finish..." e "Email Agent shutdown — pool closed" sem traceback.

- [ ] **Step 7: Commit final (se houver algo — tipicamente nada)**

Se nada mudou, skip. Senão:
```bash
git status
# review e commit ajustes pontuais se necessário
```

---

## Rollback Plan

Se algo falhar em produção após merge, reverter é simples — toda a mudança está isolada em 4 arquivos e 9 commits consecutivos:

```bash
# Identificar o primeiro commit desta feature
git log --oneline | grep -E "(singleton|fire-and-forget|httpx\[http2\])"
# Reverter em bloco (ajuste os hashes para os 9 commits desta implementação)
git revert <oldest>..<newest>
```

Sem mudanças em schema de DB, dependências transitivas removidas ou estado persistente — rollback é puramente de código.

---

## Success Criteria

Copiado do spec seção 11:

1. Tempo médio clique → edit_message de "loading" cai de ~2s para ≤300ms (medido em produção por 24h)
2. Webhook `/telegram/callback` retorna 200 em ≤100ms (p95)
3. Zero regressão funcional nos 8 fluxos de botão + fluxos de confirmação/reclassify/custom_reply
4. Suíte `pytest tests/test_telegram_* tests/test_webhook_fire_and_forget.py` passa 100%
5. Logs de produção sem `callback_query.id` duplicados
