"""
Email Agent - Orchestrator Principal
FastAPI app que recebe webhooks do Gmail e processa emails
"""

import os
import json
import logging
from datetime import datetime
from typing import Optional
from collections import OrderedDict
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from dotenv import load_dotenv

# Determinar diretório base do projeto (configurável via env)
BASE_DIR = Path(os.getenv("EMAIL_AGENT_BASE_DIR", Path(__file__).resolve().parent.parent))

# Carregar variáveis de ambiente (tenta .env local primeiro, depois BASE_DIR)
if Path(".env").exists():
    load_dotenv(".env")
elif (BASE_DIR / ".env").exists():
    load_dotenv(BASE_DIR / ".env")

# Configurar logging (stdout para docker/systemd, arquivo opcional)
log_handlers = [logging.StreamHandler()]
log_dir = BASE_DIR / "logs"
if log_dir.exists() or os.getenv("EMAIL_AGENT_LOG_FILE"):
    log_dir.mkdir(parents=True, exist_ok=True)
    log_handlers.append(logging.FileHandler(log_dir / "email_agent.log"))

from orchestrator.middleware.request_id import RequestIdFilter, RequestIdMiddleware

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [req:%(request_id)s] %(name)s - %(levelname)s - %(message)s',
    handlers=log_handlers
)

# Add request_id filter to all handlers
_rid_filter = RequestIdFilter()
for handler in logging.root.handlers:
    handler.addFilter(_rid_filter)

logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)

# Importar serviços
import asyncpg
from contextlib import asynccontextmanager
from orchestrator.settings import get_settings
from orchestrator.services.database_service import DatabaseService
from orchestrator.utils.pdf_reader import PdfReader
from orchestrator.services.qdrant_service import QdrantService
from orchestrator.services.llm_service import LLMService
from orchestrator.services.gmail_service import GmailService
from orchestrator.services.telegram_service import TelegramService
from orchestrator.handlers.email_processor import EmailProcessor
from orchestrator.services.learning_engine import LearningEngine
from orchestrator.services.metrics_service import MetricsService
from orchestrator.services.alert_service import AlertService
from orchestrator.services.job_queue import JobQueue

# Serviços que não precisam de init async ficam no nível de módulo
qdrant = QdrantService()
llm = LLMService()
gmail = GmailService()
telegram = TelegramService()

# Estes serão inicializados no lifespan (precisam de pool async)
db: DatabaseService = None
pdf_reader: PdfReader = None
learning: LearningEngine = None
processor: EmailProcessor = None
metrics: MetricsService = None
alerts: AlertService = None
job_queue: JobQueue = None


@asynccontextmanager
async def lifespan(app_instance):
    global db, pdf_reader, learning, processor, metrics, alerts, job_queue
    _settings = get_settings()

    # Create DB pool
    pool = await asyncpg.create_pool(
        dsn=_settings.database_url, min_size=2, max_size=10
    )
    db = DatabaseService(pool)
    pdf_reader = PdfReader(
        vision_model=_settings.llm_vision_model,
        openrouter_key=_settings.openrouter_api_key,
    )

    # Phase 2 services
    metrics = MetricsService(pool)
    alerts = AlertService(
        bot_token=_settings.telegram_bot_token,
        alert_user_id=_settings.telegram_alert_user_id,
        throttle_minutes=_settings.alert_throttle_minutes,
    )
    job_queue = JobQueue(pool, max_attempts=_settings.job_max_attempts)

    learning = LearningEngine(qdrant, telegram)
    processor = EmailProcessor(db, qdrant, llm, gmail, telegram, learning, pdf_reader, metrics, job_queue)

    # Background workers
    import asyncio

    async def retry_worker():
        """Retries failed jobs every 60 seconds."""
        while True:
            try:
                jobs = await job_queue.get_pending(limit=5)
                for job in jobs:
                    try:
                        if job["job_type"] == "process_email":
                            payload = json.loads(job["payload"]) if isinstance(job["payload"], str) else job["payload"]
                            result = await processor.process_email(payload["email_id"], payload["account"])
                            if result.get("status") == "error":
                                raise RuntimeError(result.get("error", "process_email returned error"))
                        await job_queue.mark_completed(job["id"])
                    except Exception as e:
                        is_dead = await job_queue.mark_failed(job["id"], str(e))
                        if is_dead:
                            await alerts.alert("job_dead", f"Job #{job['id']} ({job['job_type']}) died: {e}")
            except Exception as e:
                logger.error(f"Retry worker error: {e}")
            await asyncio.sleep(60)

    async def maintenance_worker():
        """Daily maintenance — cleanup old metrics. Runs once at startup, then every 24h."""
        while True:
            try:
                result = await metrics.cleanup(retention_days=_settings.metrics_retention_days)
                logger.info(f"Metrics cleanup: {result}")
            except Exception as e:
                logger.error(f"Metrics cleanup error: {e}")
            await asyncio.sleep(86400)

    retry_task = asyncio.create_task(retry_worker())
    maint_task = asyncio.create_task(maintenance_worker())

    logger.info("Email Agent v2.0 started — PostgreSQL connected, workers running")
    yield

    # Graceful shutdown
    retry_task.cancel()
    maint_task.cancel()
    try:
        await retry_task
    except asyncio.CancelledError:
        pass
    try:
        await maint_task
    except asyncio.CancelledError:
        pass
    await pool.close()
    logger.info("Email Agent shutdown — pool closed")


app = FastAPI(title="Email Agent", version="2.0.0", lifespan=lifespan)
app.add_middleware(RequestIdMiddleware)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "Rate limit excedido. Tente novamente em breve."})

# Cache de emails já processados (deduplicação LRU)
_processed_emails: OrderedDict = OrderedDict()
MAX_PROCESSED_CACHE = 1000


class GmailWebhookPayload(BaseModel):
    """Payload do webhook do Gmail via GOG"""
    message: dict
    subscription: Optional[str] = None
    token: Optional[str] = None


@app.get("/health")
async def health_check():
    """Endpoint de health check"""
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


@app.post("/hooks/gmail")
@limiter.limit("30/minute")
async def gmail_webhook(
    request: Request,
    background_tasks: BackgroundTasks
):
    """
    Webhook para receber notificações de novo email do Gmail

    O Gmail Pub/Sub envia POST com:
    - message.data: base64 encoded pubsub message
    - token: hook token para identificar conta
    """
    try:
        body = await request.json()
        logger.info(f"Webhook recebido: {json.dumps(body)[:500]}")

        # Validar token (pode vir do body ou query param)
        token = body.get("token")
        if not token:
            from urllib.parse import parse_qs
            query_params = parse_qs(request.url.query)
            token_list = query_params.get("token", [])
            if token_list:
                token = token_list[0]

        if not token:
            raise HTTPException(status_code=401, detail="Token não fornecido")

        # Identificar conta pelo token
        account = get_account_by_token(token)
        if not account:
            raise HTTPException(status_code=401, detail="Token inválido")

        # Extrair email_id ou history_id do payload
        message_data = body.get("message", {})
        pubsub_message = None
        email_id = None
        history_id = None

        if "data" in message_data:
            import base64
            decoded = base64.urlsafe_b64decode(message_data["data"])
            pubsub_message = json.loads(decoded)
            email_id = pubsub_message.get("emailId") or pubsub_message.get("messageId")
            history_id = pubsub_message.get("historyId")
        else:
            email_id = message_data.get("emailId") or body.get("emailId")
            history_id = message_data.get("historyId") or body.get("historyId")

        if not email_id and not history_id:
            raise HTTPException(status_code=400, detail="Nenhum identificador encontrado (emailId ou historyId)")

        logger.info(f"Processando - email_id: {email_id}, history_id: {history_id}, conta: {account}")

        # Processar em background
        async def process_with_history():
            import asyncio

            messages_from_payload = pubsub_message.get("messages", []) if pubsub_message else []

            if messages_from_payload:
                logger.info(f"Processando {len(messages_from_payload)} emails do payload direto")
                for msg in messages_from_payload:
                    msg_id = msg.get("id")
                    if msg_id and not _is_duplicate(msg_id):
                        await processor.process_email(msg_id, account)
            elif history_id:
                message_ids = await gmail.get_history(str(history_id), account)
                for msg_id in message_ids:
                    if not _is_duplicate(msg_id):
                        await processor.process_email(msg_id, account)

            if email_id and not _is_duplicate(email_id):
                await processor.process_email(email_id, account)

        background_tasks.add_task(process_with_history)

        return JSONResponse(
            status_code=200,
            content={
                "status": "accepted",
                "email_id": email_id,
                "account": account,
                "timestamp": datetime.utcnow().isoformat()
            }
        )

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="JSON inválido")
    except Exception as e:
        logger.error(f"Erro no webhook: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/telegram/callback")
@limiter.limit("60/minute")
async def telegram_callback(request: Request):
    """
    Endpoint para processar callbacks dos botões inline do Telegram.
    Validação via secret token header.
    """
    # Validar secret token do Telegram
    expected_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET")
    if expected_secret:
        received_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if received_secret != expected_secret:
            raise HTTPException(status_code=403, detail="Secret token inválido")

    try:
        body = await request.json()
        logger.info(f"Callback recebido: {json.dumps(body)[:500]}")

        callback_query = body.get("callback_query")
        if not callback_query:
            return JSONResponse(status_code=200, content={"status": "ignored"})

        callback_id = callback_query.get("id")
        callback_data = callback_query.get("data", "")
        message = callback_query.get("message", {})
        chat_id = message.get("chat", {}).get("id")

        parts = callback_data.split(":")
        action_type = parts[0] if parts else "unknown"
        email_id = parts[1] if len(parts) > 1 else ""
        account = parts[2] if len(parts) > 2 else ""

        logger.info(f"Callback: {action_type} para email {email_id}")

        action_responses = {
            "archive": "🗑️ Email arquivado",
            "create_task": "📋 Task criada",
            "schedule": "📅 Agendado",
            "read": "✅ Email lido",
            "keep": "📍 Email mantido",
            "cancel": "❌ Cancelado",
            "send_draft": "✏️ Resposta enviada",
            "edit_draft": "📝 Editando rascunho..."
        }

        response_text = action_responses.get(action_type, f"Ação: {action_type}")

        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"https://api.telegram.org/bot{telegram.bot_token}/answerCallbackQuery",
                json={"callback_query_id": callback_id, "text": response_text}
            )

            # Executar ação real
            if action_type == "archive" and email_id and account:
                await gmail.archive_email(email_id, account)
            elif action_type == "create_task" and email_id:
                if db:
                    account_data = await db.get_account(account)
                    if account_data:
                        await db.create_task(account_data["id"], f"Email: {email_id}", "Média", email_id)

            if chat_id and message.get("message_id"):
                original_text = message.get("text", "")
                new_text = f"{original_text}\n\n✅ {response_text}"
                await client.post(
                    f"https://api.telegram.org/bot{telegram.bot_token}/editMessageText",
                    json={
                        "chat_id": chat_id,
                        "message_id": message.get("message_id"),
                        "text": new_text,
                        "parse_mode": "HTML"
                    }
                )

        return JSONResponse(status_code=200, content={"status": "ok"})

    except Exception as e:
        logger.error(f"Erro no callback: {e}", exc_info=True)
        return JSONResponse(status_code=200, content={"status": "error"})


@app.post("/hooks/gmail/test")
async def test_webhook(request: Request):
    """Endpoint para testar o webhook manualmente"""
    body = await request.json()
    email_id = body.get("emailId")
    account = body.get("account")

    if not email_id or not account:
        raise HTTPException(status_code=400, detail="emailId e account são obrigatórios")

    logger.info(f"TESTE: Processando email {email_id}")
    result = await processor.process_email(email_id, account)
    return JSONResponse(content=result)


def _is_duplicate(email_id: str) -> bool:
    """Verifica se email já foi processado (deduplicação LRU in-memory)"""
    if email_id in _processed_emails:
        _processed_emails.move_to_end(email_id)
        logger.info(f"Email {email_id} já processado, pulando (dedup)")
        return True
    _processed_emails[email_id] = True
    # Evictar os mais antigos quando exceder o limite
    while len(_processed_emails) > MAX_PROCESSED_CACHE:
        _processed_emails.popitem(last=False)
    return False


def get_account_by_token(token: str) -> Optional[str]:
    """Identifica a conta pelo hook token via Settings"""
    import hmac
    _settings = get_settings()
    for email, hook_token in _settings.gmail_accounts.items():
        # hook_token could be env var name or direct value
        token_value = os.getenv(hook_token, hook_token)
        if hmac.compare_digest(token_value, token):
            return email
    return None


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8787)