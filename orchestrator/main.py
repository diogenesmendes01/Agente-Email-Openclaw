"""
Email Agent - Orchestrator Principal
FastAPI app que recebe webhooks do Gmail e processa emails
"""

import os
import json
import logging
import re
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

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=log_handlers
)
logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Email Agent", version="1.0.0")
app.state.limiter = limiter

ALLOWED_CALLBACK_ACTIONS = {
    "archive",
    "create_task",
    "schedule",
    "read",
    "keep",
    "cancel",
    "send_draft",
    "edit_draft",
}


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """Apply a baseline set of browser-facing security headers."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


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
from orchestrator.security import (
    constant_time_equals,
    extract_bearer_token,
    is_telegram_actor_allowed,
    is_valid_account,
    is_valid_email_id,
    truncate_identifier,
)

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
        logger.info("Webhook Gmail recebido (keys=%s)", sorted(body.keys()))

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

        if email_id and not is_valid_email_id(email_id):
            raise HTTPException(status_code=400, detail="emailId invÃ¡lido")
        if history_id and not re.fullmatch(r"[A-Za-z0-9_-]{4,128}", str(history_id)):
            raise HTTPException(status_code=400, detail="historyId invÃ¡lido")
        if not is_valid_account(account):
            raise HTTPException(status_code=400, detail="Conta invÃ¡lida")

        logger.info(
            "Processando webhook Gmail - email_id=%s history_id=%s conta=%s",
            truncate_identifier(email_id),
            truncate_identifier(str(history_id) if history_id else ""),
            account,
        )

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
    # Validar secret token do Telegram
    expected_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET")
    if not expected_secret:
        raise HTTPException(status_code=503, detail="Telegram webhook secret não configurado")

    received_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not constant_time_equals(expected_secret, received_secret):
        raise HTTPException(status_code=403, detail="Secret token inválido")

    try:
        body = await request.json()
        logger.info("Callback Telegram recebido (keys=%s)", sorted(body.keys()))

        callback_query = body.get("callback_query")
        if not callback_query:
            return JSONResponse(status_code=200, content={"status": "ignored"})

        callback_id = callback_query.get("id")
        callback_data = callback_query.get("data", "")
        message = callback_query.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        actor_id = callback_query.get("from", {}).get("id")

        is_allowed, reason = is_telegram_actor_allowed(actor_id, chat_id)
        if not is_allowed:
            logger.warning(
                "Callback Telegram bloqueado (reason=%s actor_id=%s chat_id=%s)",
                reason,
                actor_id,
                chat_id,
            )
            raise HTTPException(status_code=403, detail="Usuário do Telegram não autorizado")

        parts = callback_data.split(":")
        action_type = parts[0] if parts else "unknown"
        email_id = parts[1] if len(parts) > 1 else ""
        account = parts[2] if len(parts) > 2 else ""

        if action_type not in ALLOWED_CALLBACK_ACTIONS:
            raise HTTPException(status_code=400, detail="AÃ§Ã£o invÃ¡lida")

        if action_type != "cancel":
            if not is_valid_email_id(email_id):
                raise HTTPException(status_code=400, detail="emailId invÃ¡lido")
            if not is_valid_account(account):
                raise HTTPException(status_code=400, detail="Conta invÃ¡lida")

        logger.info("Callback Telegram: action=%s email=%s", action_type, truncate_identifier(email_id))

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

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro no callback: {e}", exc_info=True)
        return JSONResponse(status_code=200, content={"status": "error"})


@app.post("/hooks/gmail/test")
@limiter.limit("5/minute")
async def test_webhook(request: Request):
    """Endpoint para testar o webhook manualmente"""
    expected_token = os.getenv("EMAIL_AGENT_TEST_WEBHOOK_TOKEN", "").strip()
    if not expected_token:
        raise HTTPException(status_code=404, detail="Not found")

    provided_token = (
        request.headers.get("X-Test-Webhook-Token", "").strip()
        or extract_bearer_token(request.headers.get("Authorization"))
    )
    if not constant_time_equals(expected_token, provided_token):
        raise HTTPException(status_code=403, detail="Token de teste inválido")

    body = await request.json()
    email_id = body.get("emailId")
    account = body.get("account")

    if not email_id or not account:
        raise HTTPException(status_code=400, detail="emailId e account são obrigatórios")

    if not is_valid_email_id(email_id):
        raise HTTPException(status_code=400, detail="emailId invÃ¡lido")
    if not is_valid_account(account):
        raise HTTPException(status_code=400, detail="account invÃ¡lido")

    logger.info("TESTE: Processando email %s", truncate_identifier(email_id))
    result = await processor.process_email(email_id, account)
    return JSONResponse(content=result)


def _is_duplicate(email_id: str) -> bool:
    """Verifica se email já foi processado (deduplicação LRU in-memory)"""
    if email_id in _processed_emails:
        _processed_emails.move_to_end(email_id)
        logger.info("Email %s jÃ¡ processado, pulando (dedup)", truncate_identifier(email_id))
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
            if constant_time_equals(acct_token, token):
                return email_addr
    except Exception as e:
        logger.error(f"Erro ao carregar config.json: {e}")

    # Fallback: token direto de env var (sem hardcode)
    fallback_token = os.getenv("GOG_HOOK_TOKEN")
    fallback_account = os.getenv("GOG_HOOK_ACCOUNT")
    if constant_time_equals(fallback_token, token) and fallback_account:
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
