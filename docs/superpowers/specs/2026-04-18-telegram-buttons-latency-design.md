# Telegram Buttons Latency — Design Spec

**Data:** 2026-04-18
**Status:** Aprovado para implementação
**Escopo:** Reduzir latência percebida ao clicar em botões inline do Telegram

---

## 1. Problema

Ao clicar em qualquer botão inline (arquivar, VIP, criar tarefa, custom reply, reclassificar, etc.), o usuário percebe uma latência longa entre o clique e o feedback visual na mensagem. O toast ("mensagemzinha na tela preta" via `answerCallbackQuery`) aparece rápido, mas a edição do texto da mensagem e a execução da ação levam 2-15 segundos.

Em contraste, bots internos do Telegram (@BotFather) respondem quase instantaneamente. A pesquisa confirmou que @BotFather é inalcançável (roda dentro da infra Telegram com MTProto), mas bots externos bem otimizados atingem 100-300ms consistentes.

## 2. Causa raiz (confirmada por leitura de código)

Três falhas no design atual de `telegram_service.py`, `telegram_callbacks.py` e `main.py`:

### 2.1. `httpx.AsyncClient` recriado a cada chamada
Nove funções em `telegram_service.py` fazem `async with httpx.AsyncClient(timeout=30.0) as client:` dentro do corpo da função. Cada criação paga:
- 1 RTT TCP handshake
- 1-2 RTT TLS 1.3 handshake
- Custo total estimado: **200-500ms por call**

Um callback típico faz 3-4 calls ao Telegram → **600-2000ms só em handshakes desperdiçados**.

Funções afetadas: `_send_message`, `send_confirmation`, `edit_message`, `set_webhook`, `answer_callback`, `edit_reply_markup`, `delete_message`, `send_text`, `disable_buttons`.

### 2.2. Webhook bloqueia até a ação terminar
O endpoint `/telegram/callback` em `orchestrator/main.py` faz `await handle_callback(callback_query, services)` antes de retornar 200 ao Telegram.

Consequência dupla:
- Gmail API / LLM demoram 2-15s → Telegram vê webhook lento → pode **reenviar o update** (Telegram retenta após timeout ~60s), causando double-execution
- Usuário não recebe sinal imediato de que o clique foi aceito

### 2.3. `answerCallbackQuery` sequencial com edit_message
Em `handle_callback`, o padrão é:
```python
await tg.answer_callback(callback_id, "...")   # 1 RTT
await tg.edit_message(message_id, loading)      # 1 RTT
await action_fn(ctx)                            # 2-15s
await tg.edit_message(message_id, done)         # 1 RTT
```

Os dois primeiros `await` são independentes e poderiam rodar em paralelo (economia ~1 RTT = 200-500ms).

## 3. Solução

Três mudanças pontuais, sem dependências novas, sem migração de framework.

### 3.1. Singleton `httpx.AsyncClient` com pool e HTTP/2
- Um único `AsyncClient` criado no `__init__` de `TelegramService`, reutilizado em todas as 9 funções
- `http2=True` para multiplexing em bursts de edits
- `max_keepalive_connections=50`, `keepalive_expiry=30.0` para manter conexão quente
- Método `aclose()` chamado no lifespan shutdown do FastAPI

**Ganho esperado:** -200 a -500ms por call.

### 3.2. Webhook fire-and-forget
- `/telegram/callback` retorna 200 em <50ms
- `handle_callback` roda via `asyncio.create_task` em background
- Set global de tasks com `add_done_callback(discard)` para evitar GC prematuro (bug conhecido do CPython)
- Shutdown aguarda tasks pendentes com timeout de 5s

**Ganho esperado:** webhook responde em <50ms (independente da ação); Telegram nunca retenta por timeout.

### 3.3. `answerCallbackQuery` paralelo
- `answer_callback` disparado com `asyncio.create_task` (não `await`) no topo de cada branch do `handle_callback`
- Resto do handler prossegue sem esperar o ACK

**Ganho esperado:** -200 a -500ms (elimina 1 RTT do caminho crítico).

**Ganho total estimado:** clique → feedback visual de ~2-3s para ~100-300ms.

## 4. Arquitetura

### Fluxo atual
```
Telegram → POST /telegram/callback
              │
              └─ await handle_callback()  ← bloqueia 2-15s
                  ├─ new AsyncClient → answer_callback    (200-500ms)
                  ├─ new AsyncClient → edit_message       (200-500ms)
                  ├─ new AsyncClient → Gmail API          (2-15s)
                  └─ new AsyncClient → edit_message       (200-500ms)
              retorna 200 ← só aqui
```

### Fluxo novo
```
Telegram → POST /telegram/callback
              │
              ├─ asyncio.create_task(handle_callback())  ← fire-and-forget
              └─ retorna 200  ← <50ms

           (em background, usando client singleton com pool)
           ├─ create_task(answer_callback)  ┐
           │                                 ├─ paralelo
           └─ edit_message(loading)          ┘  (mesma conexão HTTP/2)
              └─ ação pesada (Gmail/LLM)
                 └─ edit_message(final)
```

## 5. Componentes e mudanças

| Arquivo | Mudança | Tamanho estimado |
|---|---|---|
| `orchestrator/services/telegram_service.py` | AsyncClient singleton no `__init__`; remover `async with httpx.AsyncClient()` das 9 funções; adicionar método `aclose()` | ~100 linhas alteradas |
| `orchestrator/main.py` | `/telegram/callback` fire-and-forget; lifespan shutdown aguarda tasks e fecha client | ~25 linhas |
| `orchestrator/handlers/telegram_callbacks.py` | `answer_callback` via `create_task` nos ~12 pontos de entrada (localizar via `grep "await tg.answer_callback("`) | ~20 linhas |
| `tests/test_telegram_service.py` | Ajustar mocks para cliente singleton (usar `AsyncMock` no `_client.post`) | ~30 linhas |
| `tests/test_telegram_callbacks.py` | Ajustar mocks de `answer_callback` que agora é fire-and-forget | ~15 linhas |
| `tests/test_telegram_commands.py` | Verificar se precisa ajuste (handle_text_message também vira fire-and-forget) | TBD na implementação |

**Sem mudanças em:** actions (archive, vip, etc.), DB, Gmail service, LLM, prompt builder, playbooks.

## 6. Detalhes de implementação

### 6.1. `TelegramService.__init__`
```python
class TelegramService:
    def __init__(self):
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.api_base = f"https://api.telegram.org/bot{self.bot_token}"
        self._configured = bool(self.bot_token)

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

    async def aclose(self):
        await self._client.aclose()
```

### 6.2. Padrão novo nas funções
```python
# Antes:
async with httpx.AsyncClient(timeout=30.0) as client:
    response = await client.post(f"{self.api_base}/sendMessage", json=payload)

# Depois:
response = await self._client.post("/sendMessage", json=payload)
```

O `@_retry_external` do tenacity permanece intacto (já está decorando os métodos) — continua funcionando com o client compartilhado.

### 6.3. Fire-and-forget em `main.py`
```python
_bg_tasks: set[asyncio.Task] = set()

def _fire_and_forget(coro) -> asyncio.Task:
    t = asyncio.create_task(coro)
    _bg_tasks.add(t)
    t.add_done_callback(_log_task_result)
    t.add_done_callback(_bg_tasks.discard)
    return t

def _log_task_result(task: asyncio.Task):
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.error(f"Background task failed: {exc}", exc_info=exc)

@app.post("/telegram/callback")
@limiter.limit("60/minute")
async def telegram_callback(request: Request):
    # secret validation + body parse continuam iguais
    callback_query = body.get("callback_query")
    if callback_query:
        _fire_and_forget(handle_callback(callback_query, services))
        return JSONResponse(status_code=200, content={"status": "ok"})

    message = body.get("message")
    if message and message.get("text"):
        _fire_and_forget(handle_text_message(message, services))
        return JSONResponse(status_code=200, content={"status": "ok"})

    return JSONResponse(status_code=200, content={"status": "ignored"})
```

### 6.4. Lifespan shutdown
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup (código atual preservado)
    yield
    # shutdown
    if _bg_tasks:
        logger.info(f"Waiting for {len(_bg_tasks)} background tasks to finish...")
        await asyncio.wait(_bg_tasks, timeout=5.0)
    await telegram.aclose()
```

### 6.5. `answer_callback` paralelo em `handle_callback`
Nos ~12 pontos onde o código hoje faz (localizar exaustivamente via `grep "await tg.answer_callback("`):
```python
await tg.answer_callback(callback_id, "...")
```
Trocar por:
```python
asyncio.create_task(tg.answer_callback(callback_id, "..."))
```

O resto do handler prossegue sem esperar. Se o `answer_callback` falhar, já tem log dentro da função (já retorna `False` silenciosamente em erro).

## 7. Tratamento de erros

| Cenário | Comportamento |
|---|---|
| Gmail API falha dentro da task em background | `add_done_callback` loga exceção; usuário vê edit_message com erro (código atual já trata) |
| `answer_callback` falha (toast não aparece) | Log apenas; não afeta ação principal |
| Task crashada com exceção não tratada | `_log_task_result` captura e loga; processo continua |
| Processo recebe SIGKILL com tasks pendentes | Ação perdida; usuário pode reclicar. Aceitável para bot de notificações |
| Shutdown gracioso (SIGTERM) | Lifespan aguarda 5s pelas tasks; fecha HTTP client |
| Rate limit do Telegram (429) | Tenacity `@_retry_external` continua ativo com backoff exponencial; pool compartilhado não afeta isso |
| HTTP/2 connection reset | httpx faz retry automático transparente na mesma chamada |

### 7.1. Mudança semântica importante: fim do retry de webhook

**Hoje:** o endpoint `/telegram/callback` tem `try/except` que retorna 500 em erros transitórios (DB, Gmail, LLM), e o Telegram **retenta** o update. Isso esconde falhas transitórias do usuário (a ação eventualmente completa).

**Depois:** como `handle_callback` roda em `asyncio.create_task`, exceções **não propagam** ao HTTP response. O webhook sempre retorna 200. Consequência: **erros transitórios não são mais retentados pelo Telegram**; a ação falha "silenciosamente" (com log) e o usuário precisa reclicar.

**Decisão:** aceitável para este bot. Motivos:
- Bot de notificação de email, não fluxo crítico
- O código atual de cada action já tem retry interno onde importa (tenacity nas chamadas Gmail, por exemplo)
- Double-execution era risco real antes (Telegram retenta em 60s; muitas ações não são idempotentes — ex: `send_draft` enviaria email duplicado)
- `_log_task_result` deixa rastro auditável para diagnóstico

**Não confundir isso com regressão durante review** — é mudança intencional de semântica documentada aqui.

## 8. Verificação

### 8.1. Medição objetiva
Adicionar telemetria temporária (pode ser removida após validação):
- `time.perf_counter()` no início do `/telegram/callback` e no retorno do `JSONResponse` → medir tempo de ACK do webhook
- Log dentro de `handle_callback` com duração total do handler
- **Alvo:** webhook retorna em <100ms; handler completo (excluindo Gmail/LLM) em <500ms

### 8.2. Smoke test manual
Clicar nos 8 botões de pelo menos um email e confirmar que todos respondem:
- ✉️ Enviar rascunho (com confirmação)
- 📝 Criar tarefa (com prompt de detalhes)
- ✅ Arquivar (com confirmação)
- ⭐ Marcar VIP (com confirmação)
- 💬 Responder custom (fluxo de instrução)
- 🔄 Reclassificar (fluxo de urgência)
- 🔇 Silenciar (com confirmação)
- 🗑️ Spam (com confirmação)

### 8.3. Teste de regressão
Rodar toda a suíte de testes relacionados:
```bash
pytest tests/test_telegram_service.py tests/test_telegram_callbacks.py tests/test_telegram_commands.py
```

Todos devem passar sem mudança de comportamento funcional.

### 8.4. Teste de retry do Telegram
Verificar que o Telegram **não reenvia** callbacks duplicados após a mudança (webhook retorna 200 rápido). Log de `Telegram update: ...` no `main.py` deve mostrar cada `callback_query.id` único uma vez só.

## 9. Escopo explicitamente fora

- **Otimizar `gmail_service.py`**: mesmo padrão se aplicaria, mas o ganho é secundário (Gmail API tem latência própria de 500ms-2s que não é resolvível via client singleton). Decidir após medir o resultado desta mudança.
- **Redis para `pending_actions` / cache**: ganho marginal (5-20ms); só vale em alta carga.
- **Migração para aiogram ou Pyrogram**: avaliado e descartado — mesmo ganho de latência com custo de reescrita muito maior.
- **Task queue persistente (RQ/arq)**: só se `asyncio.create_task` mostrar perdas inaceitáveis de ações em produção.
- **Local Bot API Server**: irrelevante para texto curto; só valeria para uploads/downloads grandes.
- **Deploy em região Europa**: fora do escopo de código. Se o servidor atual está no Brasil, considerar depois como otimização infra separada.

## 10. Riscos e mitigações

| Risco | Probabilidade | Mitigação |
|---|---|---|
| Task em background perdida por crash do processo | Baixa | Bot de notificações tolera; usuário reclica. Se virar problema, migrar para fila persistente |
| `httpx.AsyncClient` singleton não inicializado em testes | Média | Ajustar fixtures em `conftest.py` para fornecer mock do `_client.post` |
| `asyncio.create_task` sem referência forte gera GC prematuro | Média | Set global `_bg_tasks` com `add_done_callback(discard)` mitiga o bug conhecido |
| HTTP/2 incompatibilidade com algum proxy intermediário | Baixa | httpx faz fallback transparente para HTTP/1.1 se HTTP/2 não negociar |
| Dependência `httpx[http2]` precisa instalar `h2` | Certa | Atualizar `requirements.txt` ou `pyproject.toml` |
| Conexões stale após longo idle | Baixa | `keepalive_expiry=30.0` recicla; httpx trata reconexão transparente |

## 11. Critérios de sucesso

1. Tempo médio do clique no botão → edit_message de "loading" cair de ~2s para ≤300ms (medido com perf_counter em produção por 24h)
2. Webhook `/telegram/callback` retorna 200 em ≤100ms (p95)
3. Nenhuma regressão funcional: todos os 8 fluxos de botão + fluxos de confirmação + fluxos de reclassify/custom_reply continuam funcionando
4. Suíte de testes `test_telegram_*` passa 100%
5. Logs de produção não mostram `callback_query.id` duplicado (prova de que Telegram não retenta)
