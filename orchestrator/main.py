"""
Email Agent - Orchestrator Principal
FastAPI app que recebe webhooks do Gmail e processa emails
"""

import os
import re
import json
import hmac
import logging
from datetime import datetime
from typing import Optional, Set
from collections import OrderedDict
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
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

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=log_handlers
)
logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Email Agent", version="1.0.0")
app.state.limiter = limiter


# Security headers middleware
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

app.add_middleware(SecurityHeadersMiddleware)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "Rate limit excedido. Tente novamente em breve."})

# Cache de emails já processados (deduplicação LRU)
_processed_emails: OrderedDict = OrderedDict()
MAX_PROCESSED_CACHE = 1000

# Importar serviços
from orchestrator.services.notion_service import NotionService
from orchestrator.services.qdrant_service import QdrantService
from orchestrator.services.llm_service import LLMService
from orchestrator.services.gmail_service import GmailService
from orchestrator.services.telegram_service import TelegramService
from orchestrator.handlers.email_processor import EmailProcessor
from orchestrator.services.company_service import CompanyService
from orchestrator.services.learning_engine import LearningEngine

# Inicializar serviços
notion = NotionService()
qdrant = QdrantService()
llm = LLMService()
gmail = GmailService()
telegram = TelegramService()
company = CompanyService()
learning = LearningEngine(qdrant, telegram)
processor = EmailProcessor(notion, qdrant, llm, gmail, telegram, company, learning)


class GmailWebhookPayload(BaseModel):
    """Payload do webhook do Gmail via GOG"""
    message: dict
    subscription: Optional[str] = None
    token: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    timestamp: str
    services: dict


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Endpoint de health check"""
    return HealthResponse(
        status="healthy",
        timestamp=datetime.utcnow().isoformat(),
        services={
            "notion": "connected" if notion.is_connected() else "disconnected",
            "qdrant": "connected" if qdrant.is_connected() else "disconnected",
            "llm": "configured" if llm.is_configured() else "not_configured",
            "gmail": "ready" if gmail.is_ready() else "not_ready"
        }
    )


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
        logger.info("Webhook recebido: payload com %d chaves", len(body))

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
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro no webhook: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Erro interno no processamento")


@app.post("/telegram/callback")
@limiter.limit("60/minute")
async def telegram_callback(request: Request):
    """
    Endpoint para processar callbacks dos botões inline do Telegram.
    Validação via secret token header.
    """
    # Validar secret token do Telegram (obrigatório)
    expected_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET")
    if not expected_secret:
        logger.error("TELEGRAM_WEBHOOK_SECRET não configurado")
        raise HTTPException(status_code=500, detail="Configuração de segurança ausente")

    received_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(received_secret, expected_secret):
        raise HTTPException(status_code=403, detail="Secret token inválido")

    try:
        body = await request.json()
        logger.info("Callback recebido de %s", get_remote_address(request))

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

        # Validar action_type contra whitelist
        VALID_ACTIONS = {
            "archive", "create_task", "schedule", "read", "keep", "cancel",
            "send_draft", "edit_draft", "vip", "silence", "spam",
            "custom_reply", "reclassify"
        }
        if action_type not in VALID_ACTIONS:
            logger.warning(f"Action type inválido: {action_type}")
            return JSONResponse(status_code=200, content={"status": "invalid_action"})

        # Validar formato do email_id (alfanumérico)
        if email_id and not re.match(r'^[a-zA-Z0-9_-]+$', email_id):
            logger.warning(f"email_id com formato inválido")
            return JSONResponse(status_code=200, content={"status": "invalid_email_id"})

        # Validar formato do account (email)
        if account and not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', account):
            logger.warning(f"account com formato inválido")
            return JSONResponse(status_code=200, content={"status": "invalid_account"})

        logger.info(f"Callback: {action_type} para email {email_id[:8]}...")

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
                await notion.create_task({"titulo": f"Email: {email_id}", "prioridade": "Média"}, account)

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
@limiter.limit("5/minute")
async def test_webhook(request: Request):
    """Endpoint para testar o webhook manualmente"""
    body = await request.json()
    email_id = body.get("emailId")
    account = body.get("account")

    if not email_id or not account:
        raise HTTPException(status_code=400, detail="emailId e account são obrigatórios")

    # Validar formatos
    if not re.match(r'^[a-zA-Z0-9_-]+$', email_id):
        raise HTTPException(status_code=400, detail="emailId com formato inválido")
    if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', account):
        raise HTTPException(status_code=400, detail="account com formato inválido")

    logger.info(f"TESTE: Processando email {email_id[:8]}...")
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
    """Identifica a conta pelo hook token via config.json"""
    config_path = BASE_DIR / "config.json"
    try:
        with open(config_path) as f:
            config = json.load(f)
        for email_addr, acct_config in config.get("gmail", {}).get("accounts", {}).items():
            env_var = acct_config.get("hook_token_env", "")
            acct_token = os.getenv(env_var, "")
            if acct_token and hmac.compare_digest(acct_token, token):
                return email_addr
    except Exception as e:
        logger.error(f"Erro ao carregar config.json: {e}")

    # Fallback: token direto de env var (sem hardcode)
    fallback_token = os.getenv("GOG_HOOK_TOKEN")
    fallback_account = os.getenv("GOG_HOOK_ACCOUNT")
    if fallback_token and hmac.compare_digest(fallback_token, token) and fallback_account:
        return fallback_account

    return None


@app.on_event("shutdown")
async def shutdown_event():
    """Salvar estado antes de desligar"""
    logger.info("Encerrando Email Agent... salvando estado.")
    # Limpar cache de deduplicação
    _processed_emails.clear()
    logger.info("Email Agent encerrado.")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8787)