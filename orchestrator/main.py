"""
Email Agent - Orchestrator Principal
FastAPI app que recebe webhooks do Gmail e processa emails
"""

import os
import json
import logging
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import sys
sys.path.insert(0, "/opt/email-agent")

from dotenv import load_dotenv

# Carregar variáveis de ambiente
load_dotenv('/opt/email-agent/.env')

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/opt/email-agent/logs/email_agent.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Email Agent", version="1.0.0")

# Adicionar path do projeto
import sys
sys.path.insert(0, '/opt/email-agent')

# Importar serviços
from orchestrator.services.notion_service import NotionService
from orchestrator.services.qdrant_service import QdrantService
from orchestrator.services.llm_service import LLMService
from orchestrator.services.gog_service import GOGService
from orchestrator.services.telegram_service import TelegramService
from orchestrator.handlers.email_processor import EmailProcessor

# Inicializar serviços
notion = NotionService()
qdrant = QdrantService()
llm = LLMService()
gog = GOGService()
telegram = TelegramService()
processor = EmailProcessor(notion, qdrant, llm, gog, telegram)


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
            "gog": "ready" if gog.is_ready() else "not_ready"
        }
    )


@app.post("/hooks/gmail")
async def gmail_webhook(
    request: Request,
    background_tasks: BackgroundTasks
):
    """
    Webhook para receber notificações de novo email do Gmail
    
    O GOG gmail watch envia POST com:
    - message.data: base64 encoded pubsub message
    - token: hook token para identificar conta
    """
    try:
        body = await request.json()
        logger.info(f"Webhook recebido: {json.dumps(body)[:500]}")
        
        # Validar token (pode vir do body ou query param)
        token = body.get("token")
        if not token:
            # Tentar pegar da query string
            from urllib.parse import urlencode, parse_qs
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
            # Primeiro, verificar se há messages no payload (gog forward)
            messages_from_payload = pubsub_message.get("messages", []) if pubsub_message else []
            
            if messages_from_payload:
                logger.info(f"Processando {len(messages_from_payload)} emails do payload direto")
                for msg in messages_from_payload:
                    msg_id = msg.get("id")
                    if msg_id:
                        logger.info(f"Processando email do payload: {msg_id} - {msg.get('subject', 'sem subject')}")
                        await processor.process_email(msg_id, account)
            elif history_id:
                # Buscar emails desde history_id usando gog
                import asyncio
                gog = GOGService()
                result = await asyncio.create_subprocess_exec(
                    "gog", "gmail", "history", "--since", str(history_id),
                    "--account", account, "--json", "--select=id",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await result.communicate()
                if result.returncode == 0 and stdout:
                    import json as json_mod
                    history_data = json_mod.loads(stdout.decode("utf-8"))
                    for msg in history_data.get("messages", []):
                        msg_id = msg.get("id")
                        if msg_id:
                            logger.info(f"Processando email da history: {msg_id}")
                            processor.process_email(msg_id, account)
                else:
                    logger.error(f"Erro history: {stderr.decode()}")
            
            if email_id:
                processor.process_email(email_id, account)
        
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
async def telegram_callback(request: Request):
    """
    Endpoint para processar callbacks dos botões inline do Telegram
    """
    try:
        body = await request.json()
        logger.info(f"Callback recebido: {json.dumps(body)[:500]}")
        
        # Extrair callback data
        callback_query = body.get("callback_query")
        if not callback_query:
            return JSONResponse(status_code=200, content={"status": "ignored"})
        
        callback_id = callback_query.get("id")
        callback_data = callback_query.get("data", "")
        message = callback_query.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        
        # Parse da ação
        parts = callback_data.split(":")
        action_type = parts[0] if parts else "unknown"
        email_id = parts[1] if len(parts) > 1 else ""
        
        logger.info(f"Callback: {action_type} para email {email_id}")
        
        # Respostas por ação
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
        
        # Responder ao callback (remove loading)
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"https://api.telegram.org/bot{telegram.bot_token}/answerCallbackQuery",
                json={"callback_query_id": callback_id, "text": response_text}
            )
            
            # Atualizar mensagem
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
        
        # TODO: Executar ação real (archive, criar task, etc)
        
        return JSONResponse(status_code=200, content={"status": "ok"})
        
    except Exception as e:
        logger.error(f"Erro no callback: {e}", exc_info=True)
        return JSONResponse(status_code=200, content={"status": "error"})


@app.post("/hooks/gmail/test")
async def test_webhook(request: Request):
    """Endpoint para testar o webhook manualmente"""
    body = await request.json()
    email_id = body.get("emailId", "19d7c4a1e8b2f3d")
    account = body.get("account", "diogenes.mendes01@gmail.com")
    
    logger.info(f"TESTE: Processando email {email_id}")
    
    result = await processor.process_email(email_id, account)
    
    return JSONResponse(content=result)


def get_account_by_token(token: str) -> Optional[str]:
    """Identifica a conta pelo hook token"""
    tokens = {
        os.getenv("GOG_HOOK_TOKEN", "4cfa50bd0e2ef8105a300290cdd05902ada7af51194ceea4"): "diogenes.mendes01@gmail.com",
        # Adicionar outros tokens conforme necessário
    }
    return tokens.get(token)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8787)