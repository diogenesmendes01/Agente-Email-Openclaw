#!/usr/bin/env python3
"""
Telegram Bot Poller - Processa callbacks do Email Agent Bot
Com ações reais integradas
"""

import os
import asyncio
import logging
import json
import signal
import tempfile
import httpx
from pathlib import Path
from datetime import datetime
import sys

from dotenv import load_dotenv

# Determinar diretório base
BASE_DIR = Path(os.getenv("EMAIL_AGENT_BASE_DIR", Path(__file__).resolve().parent))

# Carregar .env
if Path(".env").exists():
    load_dotenv(".env")
elif (BASE_DIR / ".env").exists():
    load_dotenv(BASE_DIR / ".env")

# Adicionar path para imports
sys.path.insert(0, str(BASE_DIR))
from vip_manager import (
    add_vip, is_vip, get_min_urgency, get_all_vips,
    add_to_blacklist, is_blacklisted, get_blacklist_reason
)

from orchestrator.services.qdrant_service import QdrantService

# Initialize Qdrant for structured feedback
_qdrant = QdrantService()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Configurações
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
NOTION_DB_TAREFAS = os.getenv("NOTION_DB_TAREFAS", "33d2b64f-5143-81b5-803d-d042dbb50525")

# Estado temporário
pending_actions = {}
pending_custom_replies = {}

# Arquivos de estado (relativos ao BASE_DIR)
FEEDBACK_FILE = str(BASE_DIR / "feedback.json")
PENDING_REPLIES_FILE = str(BASE_DIR / "pending_replies.json")
PENDING_ACTIONS_FILE = str(BASE_DIR / "pending_actions.json")

# API OpenRouter para LLM
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

def _atomic_write_json(filepath: str, data) -> bool:
    """Escrita atômica em JSON - evita corrupção se o processo crashar"""
    try:
        dir_path = os.path.dirname(filepath) or "."
        fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, filepath)  # Atômico no mesmo filesystem
            return True
        except Exception:
            os.unlink(tmp_path)
            raise
    except Exception as e:
        logger.error(f"Erro ao salvar {filepath}: {e}")
        return False


def _load_json(filepath: str, default=None):
    """Carrega JSON com fallback seguro"""
    if default is None:
        default = {}
    try:
        if os.path.exists(filepath):
            with open(filepath, 'r') as f:
                return json.load(f)
    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"Erro ao carregar {filepath}: {e}")
    return default


def load_pending_actions():
    """Carrega pending_actions do arquivo"""
    return _load_json(PENDING_ACTIONS_FILE, {})


def save_pending_actions():
    """Salva pending_actions no arquivo (atômico)"""
    _atomic_write_json(PENDING_ACTIONS_FILE, pending_actions)


async def run_gog(cmd_args: list, account: str, timeout: float = 30) -> tuple:
    """Executa GOG de forma assíncrona. Retorna (success: bool, output: str)"""
    env = os.environ.copy()
    env["GOG_KEYRING_PASSWORD"] = os.getenv("GOG_KEYRING_PASSWORD", "")
    env["GOG_ACCOUNT"] = account
    try:
        proc = await asyncio.create_subprocess_exec(
            "gog", *cmd_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode == 0:
            return True, stdout.decode("utf-8", errors="replace")
        else:
            return False, stderr.decode("utf-8", errors="replace")
    except asyncio.TimeoutError:
        proc.kill()
        return False, "Timeout"
    except Exception as e:
        return False, str(e)


URGENCY_EMOJIS = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🟢",
    "ignore": "⚪"
}

async def get_updates(client: httpx.AsyncClient, offset: int = 0) -> list:
    """Busca updates do Telegram"""
    try:
        response = await client.get(
            f"{API_BASE}/getUpdates",
            params={"offset": offset, "timeout": 30}
        )
        data = response.json()
        return data.get("result", [])
    except Exception as e:
        logger.error(f"Erro ao buscar updates: {e}")
        return []

async def answer_callback(client: httpx.AsyncClient, callback_id: str, text: str):
    """Responde ao callback"""
    try:
        await client.post(
            f"{API_BASE}/answerCallbackQuery",
            json={"callback_query_id": callback_id, "text": text}
        )
    except Exception as e:
        logger.error(f"Erro ao responder callback: {e}")

async def send_message(client: httpx.AsyncClient, chat_id: int, text: str, thread_id: int = 11, reply_markup: dict = None):
    """Envia mensagem no tópico"""
    try:
        payload = {
            "chat_id": chat_id,
            "message_thread_id": thread_id,
            "text": text,
            "parse_mode": "HTML"
        }
        
        if reply_markup:
            payload["reply_markup"] = reply_markup
        
        await client.post(
            f"{API_BASE}/sendMessage",
            json=payload
        )
    except Exception as e:
        logger.error(f"Erro ao enviar mensagem: {e}")

async def edit_message_text(client: httpx.AsyncClient, chat_id: int, message_id: int, text: str, reply_markup: dict = None):
    """Edita mensagem existente no Telegram"""
    try:
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML"
        }
        
        if reply_markup:
            payload["reply_markup"] = reply_markup
        
        await client.post(
            f"{API_BASE}/editMessageText",
            json=payload
        )
    except Exception as e:
        logger.error(f"Erro ao editar mensagem: {e}")

async def save_feedback(email_id: str, sender: str, original_urgency: str, corrected_urgency: str, keywords: list):
    """Salva feedback de reclassificação em feedback.json (backup) e Qdrant (primário)"""
    try:
        # Backup: feedback.json
        feedback_data = _load_json(FEEDBACK_FILE, [])
        feedback_data.append({
            "email_id": email_id,
            "from": sender,
            "original_urgency": original_urgency,
            "corrected_urgency": corrected_urgency,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "keywords": keywords
        })
        _atomic_write_json(FEEDBACK_FILE, feedback_data)

        # Primary: Qdrant structured feedback
        if _qdrant.is_connected():
            await _qdrant.update_feedback(
                email_id=email_id,
                feedback="corrected",
                original_priority=original_urgency,
                corrected_priority=corrected_urgency,
            )

        logger.info(f"Feedback salvo: {email_id[:15]} | {original_urgency} -> {corrected_urgency}")
    except Exception as e:
        logger.error(f"Erro ao salvar feedback: {e}")

def extract_urgency_from_message(text: str) -> str:
    """Extrai urgência original da mensagem"""
    text_lower = text.lower()
    if "🔴 critical" in text_lower or "urgência: critical" in text_lower:
        return "critical"
    elif "🟠 high" in text_lower or "urgência: high" in text_lower:
        return "high"
    elif "🟡 medium" in text_lower or "urgência: medium" in text_lower:
        return "medium"
    elif "🟢 low" in text_lower or "urgência: low" in text_lower:
        return "low"
    return "medium"  # default

def extract_keywords_from_message(text: str) -> list:
    """Extrai keywords da mensagem (se houver)"""
    keywords = []
    if "Keywords:" in text or "Palavras-chave:" in text:
        for line in text.split("\n"):
            if "Keywords:" in line or "Palavras-chave:" in line:
                kw_str = line.split(":")[-1].strip()
                keywords = [k.strip() for k in kw_str.split(",") if k.strip()]
                break
    return keywords

# ============================================================
# FUNÇÕES PARA RESPONDER CUSTOM
# ============================================================

async def generate_custom_reply(email_content: str, instruction: str) -> str:
    """Gera resposta customizada via GLM-5 Turbo"""
    try:
        async with httpx.AsyncClient(timeout=60.0) as llm_client:
            response = await llm_client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
                json={
                    "model": "z-ai/glm-5-turbo",
                    "messages": [
                        {"role": "system", "content": "Escreva respostas de email profissionais em português. Seja direto e formal. Assine como 'Att, Diógenes Mendes'"},
                        {"role": "user", "content": f"Email recebido:\n{email_content[:2000]}\n\nInstrução do usuário: {instruction}\n\nEscreva a resposta:"}
                    ],
                    "max_tokens": 800
                }
            )
            data = response.json()
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"Erro ao gerar resposta: {e}")
        return None

def save_pending_reply(email_id: str, data: dict):
    """Salva estado de custom reply pendente (atômico)"""
    pending = _load_json(PENDING_REPLIES_FILE, {})
    pending[email_id] = data
    _atomic_write_json(PENDING_REPLIES_FILE, pending)

def get_pending_reply(email_id: str) -> dict:
    """Recupera estado de custom reply pendente"""
    pending = _load_json(PENDING_REPLIES_FILE, {})
    return pending.get(email_id, {})

def clear_pending_reply(email_id: str):
    """Remove estado de custom reply pendente (atômico)"""
    pending = _load_json(PENDING_REPLIES_FILE, {})
    if email_id in pending:
        del pending[email_id]
        _atomic_write_json(PENDING_REPLIES_FILE, pending)

async def action_custom_reply_start(email_id: str, message: dict, client: httpx.AsyncClient):
    """Inicia fluxo de resposta customizada"""
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")
    
    # Extrair dados
    parts = message.get("data", "").split(":")
    account = parts[2] if len(parts) > 2 else os.getenv("GOG_HOOK_ACCOUNT", "")
    sender = parts[3] if len(parts) > 3 else ""
    
    # Salvar estado
    save_pending_reply(email_id, {
        "chat_id": chat_id,
        "message_id": message.get("message_id"),
        "email_id": email_id,
        "account": account,
        "sender": sender,
        "original_text": text,
        "waiting_instruction": True
    })
    
    await send_message(client, chat_id,
        f"💬 <b>Digite sua instrução de resposta:</b>\n\n"
        f"Exemplos:\n"
        f"• diz que entrego na sexta\n"
        f"• pede pra remarcar\n"
        f"• aceita mas pede desconto",
        reply_markup={
            "inline_keyboard": [[
                {"text": "❌ Cancelar", "callback_data": f"cancel_custom_reply:{email_id}"}
            ]]
        })

async def action_custom_reply_generate(email_id: str, instruction: str, client: httpx.AsyncClient):
    """Gera resposta customizada com LLM"""
    state = get_pending_reply(email_id)
    if not state:
        return
    
    chat_id = state.get("chat_id")
    original_text = state.get("original_text", "")
    
    # Gerar resposta
    await send_message(client, chat_id, "💬 Gerando resposta...")
    reply = await generate_custom_reply(original_text, instruction)
    
    if not reply:
        await send_message(client, chat_id, "❌ Erro ao gerar resposta. Tente novamente.")
        return
    
    # Salvar resposta gerada
    state["last_reply"] = reply
    state["waiting_instruction"] = False
    save_pending_reply(email_id, state)
    
    # Mostrar com botões
    keyboard = {
        "inline_keyboard": [[
            {"text": "✉️ Enviar este", "callback_data": f"send_custom_draft:{email_id}"},
            {"text": "✏️ Ajustar", "callback_data": f"adjust_custom_draft:{email_id}"}
        ]]
    }
    
    await send_message(client, chat_id,
        f"💬 <b>NOVO RASCUNHO:</b>\n"
        f"┌─────────────────────────────\n"
        f"│ {reply[:500]}\n"
        f"└─────────────────────────────",
        reply_markup=keyboard)

# ============================================================
# AÇÕES REAIS
# ============================================================

async def action_mark_read(email_id: str, account: str, client: httpx.AsyncClient, chat_id: int):
    """✅ Marcar como lido - Arquiva no Gmail"""
    try:
        success, output = await run_gog(
            ["gmail", "modify", email_id, "--remove-labels", "INBOX", "UNREAD"],
            account, timeout=30
        )
        if success:
            await send_message(client, chat_id,
                f"✅ <b>Email marcado como lido</b>\n📧 Arquivado no Gmail")
        else:
            await send_message(client, chat_id,
                f"⚠️ Erro ao arquivar: {output[:100]}")
    except Exception as e:
        logger.error(f"Erro em mark_read: {e}")
        await send_message(client, chat_id, f"❌ Erro: {str(e)[:100]}")

async def action_create_task(email_id: str, subject: str, urgency: str, task_details: str, client: httpx.AsyncClient, chat_id: int):
    """📋 Criar tarefa no Notion com detalhes personalizados"""
    try:
        # Mapear urgência para prioridade Notion
        PRIORITY_MAP = {
            "critical": "Crítica",
            "high": "Alta",
            "medium": "Média",
            "low": "Baixa"
        }
        
        # Se não tiver detalhes, usar o assunto do email
        if not task_details or task_details.strip() == "":
            task_details = subject
        
        title = f"[{urgency.upper()}] {task_details[:90]}"
        prioridade = PRIORITY_MAP.get(urgency, "Média")
        
        # Criar task no Notion
        async with httpx.AsyncClient(timeout=30.0) as notion_client:
            response = await notion_client.post(
                "https://api.notion.com/v1/pages",
                headers={
                    "Authorization": f"Bearer {NOTION_API_KEY}",
                    "Content-Type": "application/json",
                    "Notion-Version": "2022-06-28"
                },
                json={
                    "parent": {"database_id": NOTION_DB_TAREFAS},
                    "properties": {
                        "Name": {"title": [{"text": {"content": title}}]},
                        "Origem": {"select": {"name": "email"}},
                        "Prioridade": {"select": {"name": prioridade}},
                        "Email ID": {"rich_text": [{"text": {"content": email_id}}]}
                    }
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                page_id = data["id"]
                notion_url = f"https://notion.so/{page_id.replace('-', '')}"
                await send_message(client, chat_id,
                    f"✅ <b>Tarefa criada!</b>\n"
                    f"📝 {title[:60]}\n"
                    f"🎯 Prioridade: {prioridade}\n"
                    f"🔗 <a href='{notion_url}'>Ver no Notion</a>")
            else:
                await send_message(client, chat_id,
                    f"⚠️ Erro ao criar task: {response.status_code}")
    except Exception as e:
        logger.error(f"Erro em create_task: {e}")
        await send_message(client, chat_id, f"❌ Erro: {str(e)[:100]}")

async def action_archive(email_id: str, account: str, client: httpx.AsyncClient, chat_id: int):
    """🗑️ Arquivar email"""
    await action_mark_read(email_id, account, client, chat_id)

async def action_spam(email_id: str, account: str, sender: str, client: httpx.AsyncClient, chat_id: int, message_id: int):
    """🚫 Marcar como spam"""
    try:
        success, output = await run_gog(
            ["gmail", "modify", email_id,
             "--remove-labels", "INBOX", "UNREAD",
             "--add-labels", "SPAM"],
            account, timeout=30
        )
        if success:
            add_to_blacklist(sender, "marcado como spam pelo usuário")
            await mark_message_done(chat_id, message_id, "🚫 Marcado como spam", client, email_id)
        else:
            await send_message(client, chat_id, f"⚠️ Erro: {output[:100]}")
    except Exception as e:
        logger.error(f"Erro em spam: {e}")

async def action_vip(sender: str, client: httpx.AsyncClient, chat_id: int):
    """⭐ Adicionar remetente como VIP"""
    try:
        # Adicionar à lista VIP
        result = add_vip(sender, sender.split('@')[0] if '@' in sender else sender)
        
        if result:
            await send_message(client, chat_id,
                f"⭐ <b>VIP adicionado!</b>\n"
                f"📧 {sender}\n"
                f"🔔 Próximos emails serão classificados como ALTA prioridade")
        else:
            await send_message(client, chat_id,
                f"ℹ️ {sender} já está na lista VIP")
    except Exception as e:
        logger.error(f"Erro em vip: {e}")

async def action_silence(sender: str, client: httpx.AsyncClient, chat_id: int):
    """🔇 Silenciar remetente"""
    try:
        # Adicionar à blacklist
        result = add_to_blacklist(sender, "silenciado pelo usuário")
        
        if result:
            await send_message(client, chat_id,
                f"🔇 <b>Remetente silenciado</b>\n"
                f"📧 {sender}\n"
                f"🔕 Não receberá mais notificações")
        else:
            await send_message(client, chat_id,
                f"ℹ️ {sender} já está na blacklist")
    except Exception as e:
        logger.error(f"Erro em silence: {e}")

async def action_reclassify_start(email_id: str, original_message: dict, client: httpx.AsyncClient):
    """🔄 Troca os botões para mostrar opções de urgência"""
    chat_id = original_message.get("chat", {}).get("id")
    message_id = original_message.get("message_id")
    text = original_message.get("text", "")
    
    # Extrair dados da mensagem original
    sender = ""
    if "De:" in text or "📨" in text:
        for line in text.split("\n"):
            if "De:" in line or "📨" in line:
                sender = line.replace("De:", "").replace("📨", "").strip()
                break
    
    # Guardar estado
    pending_actions[email_id] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "sender": sender,
        "original_text": text,
        "original_urgency": extract_urgency_from_message(text),
        "keywords": extract_keywords_from_message(text)
    }
    save_pending_actions()
    
    # Trocar APENAS os botões (não o texto)
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "🔴 Critical", "callback_data": f"set_urgency:critical:{email_id}"},
                {"text": "🟠 High", "callback_data": f"set_urgency:high:{email_id}"}
            ],
            [
                {"text": "🟡 Medium", "callback_data": f"set_urgency:medium:{email_id}"},
                {"text": "🟢 Low", "callback_data": f"set_urgency:low:{email_id}"}
            ],
            [
                {"text": "❌ Cancelar", "callback_data": f"cancel_reclassify:{email_id}"}
            ]
        ]
    }
    
    # Usar editMessageReplyMarkup para só trocar os botões
    try:
        await client.post(
            f"{API_BASE}/editMessageReplyMarkup",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "reply_markup": keyboard
            }
        )
    except Exception as e:
        logger.error(f"Erro ao trocar botões: {e}")

async def action_reclassify_complete(email_id: str, new_urgency: str, client: httpx.AsyncClient, chat_id: int, message_id: int = None):
    """Completa reclassificação - edita mensagem e volta botões originais"""
    if email_id not in pending_actions:
        await send_message(client, chat_id, "❌ Erro: sessão expirada. Tente novamente.")
        return
    
    state = pending_actions[email_id]
    original_message_id = state["message_id"]
    sender = state["sender"]
    original_urgency = state["original_urgency"]
    keywords = state["keywords"]
    original_text = state["original_text"]
    
    # 1. Salvar feedback
    await save_feedback(email_id, sender, original_urgency, new_urgency, keywords)
    
    # 2. Editar texto da mensagem - trocar header de urgência
    lines = original_text.split("\n")
    updated_lines = []
    for line in lines:
        # Procurar linha que começa com emoji de urgência
        if any(emoji in line for emoji in ["🔴", "🟠", "🟡", "🟢"]):
            if "CRITICAL" in line.upper() or "HIGH" in line.upper() or "MEDIUM" in line.upper() or "LOW" in line.upper():
                # Substituir pela nova urgência
                parts = line.split("│")
                if len(parts) >= 2:
                    new_urgency_emoji = URGENCY_EMOJIS.get(new_urgency, "🟡")
                    new_urgency_text = new_urgency.upper()
                    # Reconstruir linha corretamente
                    # parts[0] = "🔴 CRITICAL " → ignorar
                    # parts[1:] = [" 💰 Financeiro ", " 95%"] → manter
                    line = f"{new_urgency_emoji} {new_urgency_text} │" + " │".join(parts[1:])
        updated_lines.append(line)
    
    new_text = "\n".join(updated_lines)
    
    # 3. Criar botões originais de volta
    # Callback data deve ser simples e curto
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✉️ Enviar", "callback_data": f"send_draft:{email_id}"},
                {"text": "📝 Tarefa", "callback_data": f"create_task:{email_id}"}
            ],
            [
                {"text": "✅ Arquivar", "callback_data": f"archive:{email_id}"},
                {"text": "⭐ VIP", "callback_data": f"vip:{email_id}"}
            ],
            [
                {"text": "💬 Custom", "callback_data": f"custom_reply:{email_id}"},
                {"text": "🔄 Reclassificar", "callback_data": f"reclassify:{email_id}"}
            ],
            [
                {"text": "🔇 Silenciar", "callback_data": f"silence:{email_id}"},
                {"text": "🗑️ Spam", "callback_data": f"spam:{email_id}"}
            ],
            [
                {"text": "🔗 Abrir no Gmail", "url": f"https://mail.google.com/mail/u/0/#inbox/{email_id}"}
            ]
        ]
    }
    
    # 4. Editar mensagem com novo texto E botões originais
    # IMPORTANTE: escapar TODO o HTML para evitar erros
    import html as html_module
    
    # Escapar todo o texto
    escaped_text = html_module.escape(new_text)
    
    # Enviar como texto plano (sem parse_mode HTML)
    success = False
    try:
        response = await client.post(
            f"{API_BASE}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": original_message_id,
                "text": escaped_text[:4000],
                "reply_markup": keyboard
            }
        )
        if response.status_code == 200:
            success = True
            logger.info(f"Mensagem editada com sucesso")
        else:
            logger.error(f"Erro ao editar: {response.status_code} - {response.text}")
            # Não limpar o estado se falhou
            return
    except Exception as e:
        logger.error(f"Erro ao editar mensagem: {e}")
        return
    
    # Limpar estado APENAS se sucesso
    if success and email_id in pending_actions:
        del pending_actions[email_id]
        save_pending_actions()
    
    logger.info(f"Reclassificação completa: {email_id[:15]} | {original_urgency} -> {new_urgency}")

async def action_send_draft(email_id: str, account: str, client: httpx.AsyncClient, chat_id: int):
    """✉️ Enviar rascunho"""
    try:
        # 1. Extrair rascunho do estado pendente (está no pending_custom_replies)
        state = get_pending_reply(email_id)
        if not state:
            await send_message(client, chat_id,
                f"❌ <b>Erro:</b> Rascunho não encontrado. O rascunho expirou.")
            return
        
        # Obter rascunho e dados do remetente
        draft_content = state.get("last_reply", "")
        sender_email = state.get("sender", "")
        original_text = state.get("original_text", "")
        
        if not draft_content:
            await send_message(client, chat_id,
                f"❌ <b>Erro:</b> Rascunho vazio.")
            return
        
        # 2. Enviar resposta via GOG (gmail reply)
        await send_message(client, chat_id,
            f"✉️ <b>Enviando resposta...</b>\n"
            f"📧 Para: {sender_email}\n"
            f"📝 Comprimento: {len(draft_content)} caracteres")

        success, output = await run_gog(
            ["gmail", "reply", email_id, "--body", draft_content],
            account, timeout=60
        )

        if success:
            await mark_message_done(chat_id, state.get("message_id"), "✅ Respondido", client, email_id)
            clear_pending_reply(email_id)
        else:
            await send_message(client, chat_id,
                f"❌ <b>Erro ao enviar resposta:</b>\n{output[:200]}")
            
    except Exception as e:
        logger.error(f"Erro em send_draft: {e}")
        await send_message(client, chat_id, f"❌ Erro interno: {str(e)[:100]}")

# ============================================================
# FUNÇÕES AUXILIARES PARA CONFIRMAÇÃO
# ============================================================

async def show_confirmation_buttons(
    client: httpx.AsyncClient,
    chat_id: int,
    message_id: int,
    action: str,
    email_id: str,
    account: str,
    sender: str,
    original_text: str
):
    """Mostra botões de confirmação para uma ação"""
    
    # Salvar estado temporário
    pending_actions[email_id] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "action": action,
        "account": account,
        "sender": sender,
        "original_text": original_text
    }
    
    # Definir texto de confirmação baseado na ação
    action_texts = {
        "silence": "🔇 <b>Silenciar Remetente</b>",
        "spam": "🚫 <b>Marcar como Spam</b>",
        "send_draft": "✉️ <b>Enviar Rascunho</b>"
    }
    
    action_warnings = {
        "silence": "⚠️ Este remetente será adicionado à blacklist.\nVocê não receberá mais notificações dele.",
        "spam": "⚠️ Este email será marcado como spam.\nO remetente será adicionado à blacklist.",
        "send_draft": "⚠️ O rascunho será enviado como resposta."
    }
    
    # Teclado de confirmação
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ Confirmar", "callback_data": f"confirm_{action}:{email_id}:{account}:{sender}"},
                {"text": "❌ Cancelar", "callback_data": f"cancel_{action}:{email_id}:{account}:{sender}"}
            ]
        ]
    }
    
    # Mostrar confirmação
    await edit_message_text(
        client, chat_id, message_id,
        f"{action_texts.get(action, '⚠️ Confirmar Ação')}\n\n"
        f"📧 Remetente: {sender}\n\n"
        f"{action_warnings.get(action, '')}\n\n"
        f"<b>Confirma esta ação?</b>",
        reply_markup=keyboard
    )

async def mark_message_done(chat_id: int, message_id: int, status: str, client: httpx.AsyncClient, email_id: str = None):
    """Adiciona status no final da mensagem e remove botões"""
    
    # Buscar texto original
    original_text = None
    if email_id and email_id in pending_actions:
        original_text = pending_actions[email_id].get("original_text")
    
    if not original_text:
        logger.warning(f"Texto original não encontrado para email_id={email_id}")
        return
    
    # Preparar timestamp formatado
    timestamp = datetime.now().strftime("%d/%m às %H:%M")
    
    # Adicionar status no final
    new_text = original_text + f"\n\n───\n{status} em {timestamp}"
    
    # Editar mensagem removendo botões
    await edit_message_text(
        client, chat_id, message_id, 
        new_text,
        reply_markup={"inline_keyboard": []}
    )
    
    # Limpar estado
    if email_id and email_id in pending_actions:
        del pending_actions[email_id]
    
    logger.info(f"Mensagem marcada como feita: {status}")

# ============================================================
# PROCESSADOR DE CALLBACKS
# ============================================================

async def process_callback(callback: dict, client: httpx.AsyncClient):
    """Processa um callback com ação real"""
    callback_id = callback["id"]
    callback_data = callback.get("data", "")
    message = callback.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")
    thread_id = message.get("message_thread_id", 11)

    if not callback_data:
        return
    
    # Parse do callback: action:email_id:account
    # Sender será extraído da mensagem original quando necessário
    parts = callback_data.split(":")
    action = parts[0] if parts else "unknown"
    email_id = parts[1] if len(parts) > 1 else ""
    account = parts[2] if len(parts) > 2 else os.getenv("GOG_HOOK_ACCOUNT", "")
    
    # Extrair sender da mensagem original
    sender = ""
    text = message.get("text", "")
    if "📨" in text:
        for line in text.split("\n"):
            if "📨" in line:
                sender = line.replace("📨", "").strip()
                # Limpar nome, pegar email se houver
                if "<" in sender:
                    # Extrair email entre <>
                    import re
                    match = re.search(r'<([^>]+)>', sender)
                    if match:
                        sender = match.group(1)
                break
    
    # Extrair assunto da mensagem original
    subject = ""
    text = message.get("text", "")
    if "Assunto:" in text:
        for line in text.split("\n"):
            if "Assunto:" in line:
                subject = line.split("Assunto:")[1].strip()
                break
    
    logger.info(f"Callback: {action} | email={email_id[:15]} | account={account}")
    
    # Callback especial: set_urgency (segunda etapa da reclassificação)
    # Formato: set_urgency:urgency:email_id
    if action == "set_urgency":
        new_urgency = parts[1] if len(parts) > 1 else "medium"
        email_id_for_urgency = parts[2] if len(parts) > 2 else ""
        
        logger.info(f"Set urgency: {new_urgency} for email {email_id_for_urgency[:15]}")
        logger.info(f"Pending actions keys: {list(pending_actions.keys())[:3]}")
        
        await answer_callback(client, callback_id, f"✅ {new_urgency.upper()}")
        await action_reclassify_complete(email_id_for_urgency, new_urgency, client, chat_id)
        return
    
    # Callback: cancel_custom_reply - cancela resposta customizada
    if action == "cancel_custom_reply":
        await answer_callback(client, callback_id, "❌ Cancelado")
        # Limpar estado
        clear_pending_reply(email_id)
        # Apagar a mensagem que pediu instrução (a que tem o botão cancelar)
        try:
            await client.post(
                f"{API_BASE}/deleteMessage",
                json={
                    "chat_id": chat_id,
                    "message_id": message.get("message_id")
                }
            )
        except Exception as e:
            logger.error(f"Erro ao apagar mensagem: {e}")
        return
    
    # Callback: cancel_reclassify - volta botões originais
    if action == "cancel_reclassify":
        await answer_callback(client, callback_id, "❌ Cancelado")
        # Voltar botões originais
        if email_id in pending_actions:
            state = pending_actions[email_id]
            
            keyboard = {
                "inline_keyboard": [
                    [
                        {"text": "✉️ Enviar", "callback_data": f"send_draft:{email_id}"},
                        {"text": "📝 Tarefa", "callback_data": f"create_task:{email_id}"}
                    ],
                    [
                        {"text": "✅ Arquivar", "callback_data": f"archive:{email_id}"},
                        {"text": "⭐ VIP", "callback_data": f"vip:{email_id}"}
                    ],
                    [
                        {"text": "💬 Custom", "callback_data": f"custom_reply:{email_id}"},
                        {"text": "🔄 Reclassificar", "callback_data": f"reclassify:{email_id}"}
                    ],
                    [
                        {"text": "🔇 Silenciar", "callback_data": f"silence:{email_id}"},
                        {"text": "🗑️ Spam", "callback_data": f"spam:{email_id}"}
                    ],
                    [
                        {"text": "🔗 Abrir no Gmail", "url": f"https://mail.google.com/mail/u/0/#inbox/{email_id}"}
                    ]
                ]
            }
            
            # Voltar botões originais
            try:
                await client.post(
                    f"{API_BASE}/editMessageReplyMarkup",
                    json={
                        "chat_id": chat_id,
                        "message_id": message.get("message_id"),
                        "reply_markup": keyboard
                    }
                )
            except Exception as e:
                logger.error(f"Erro ao voltar botões: {e}")
            
            del pending_actions[email_id]
            save_pending_actions()
        return
    
    # ========================================================================
    # HANDLERS DE CONFIRMAÇÃO E CANCELAMENTO
    # ========================================================================
    
    # CONFIRMAR SILENCE
    if action == "confirm_silence":
        await answer_callback(client, callback_id, "🔇 Silenciando...")
        await action_silence(sender, client, chat_id)
        # Marcar mensagem como feita
        if email_id in pending_actions:
            state = pending_actions[email_id]
            await mark_message_done(
                state["chat_id"], 
                state["message_id"], 
                "🔇 Silenciado", 
                client,
                email_id
            )
        return
    
    # CANCELAR SILENCE
    if action == "cancel_silence":
        await answer_callback(client, callback_id, "Cancelado")
        # Restaurar mensagem original
        if email_id in pending_actions:
            state = pending_actions[email_id]
            original_text = state.get("original_text", "Ação cancelada")
            await edit_message_text(client, chat_id, message_id, original_text)
            del pending_actions[email_id]
        return
    
    # CONFIRMAR SPAM
    if action == "confirm_spam":
        await answer_callback(client, callback_id, "🚫 Marcando como spam...")
        state = pending_actions.get(email_id, {})
        spam_msg_id = state.get("message_id", message_id)
        await action_spam(email_id, account, sender, client, chat_id, spam_msg_id)
        return
    
    # CANCELAR SPAM
    if action == "cancel_spam":
        await answer_callback(client, callback_id, "Cancelado")
        # Restaurar mensagem original
        if email_id in pending_actions:
            state = pending_actions[email_id]
            original_text = state.get("original_text", "Ação cancelada")
            await edit_message_text(client, chat_id, message_id, original_text)
            del pending_actions[email_id]
        return
    
    # CONFIRMAR ENVIO DE RASCUNHO
    if action == "confirm_send_draft":
        await answer_callback(client, callback_id, "✉️ Enviando resposta...")
        await action_send_draft(email_id, account, client, chat_id)
        # Marcar mensagem como feita
        if email_id in pending_actions:
            state = pending_actions[email_id]
            await mark_message_done(
                state["chat_id"],
                state["message_id"],
                "✉️ Resposta Enviada",
                client,
                email_id
            )
        return
    
    # CANCELAR ENVIO DE RASCUNHO
    if action == "cancel_send_draft":
        await answer_callback(client, callback_id, "Cancelado")
        # Restaurar mensagem original
        if email_id in pending_actions:
            state = pending_actions[email_id]
            original_text = state.get("original_text", "Ação cancelada")
            await edit_message_text(client, chat_id, message_id, original_text)
            del pending_actions[email_id]
        return
    
    # ========================================================================
    # RESPONDER CUSTOM
    # ========================================================================
    
    # Iniciar fluxo de resposta custom
    if action == "custom_reply":
        await answer_callback(client, callback_id, "💬 Aguardando instrução")
        await action_custom_reply_start(email_id, message, client)
        return
    
    # Enviar rascunho customizado
    if action == "send_custom_draft":
        state = get_pending_reply(email_id)
        if state and "last_reply" in state:
            await answer_callback(client, callback_id, "✉️ Enviando...")
            
            chat_id = state.get("chat_id")
            message_id = state.get("message_id")
            sender = state.get("sender", "")
            last_reply = state["last_reply"]
            account = state.get("account", "diogenes.mendes01@gmail.com")
            original_email_id = state.get("email_id", "")
            
            # Enviar via Gmail usando gog gmail reply
            try:
                success, output = await run_gog(
                    ["gmail", "reply", original_email_id, "--body", last_reply],
                    account, timeout=60
                )
                if success:
                    await mark_message_done(chat_id, message_id, "✅ Respondido", client, email_id)
                    clear_pending_reply(email_id)
                else:
                    await send_message(client, chat_id, f"❌ Erro ao enviar: {output[:100]}")
            except Exception as e:
                logger.error(f"Erro ao enviar custom reply: {e}")
                await send_message(client, chat_id, f"❌ Erro: {str(e)[:100]}")
        else:
            await send_message(client, chat_id, "❌ Rascunho não encontrado")
        return
    
    # Ajustar rascunho
    if action == "adjust_custom_draft":
        state = get_pending_reply(email_id)
        if state:
            state["waiting_instruction"] = True
            save_pending_reply(email_id, state)
            await answer_callback(client, callback_id, "✏️ Digite nova instrução")
            chat_id = message.get("chat", {}).get("id")
            await send_message(client, chat_id, "💬 <b>Digite a nova instrução:</b>")
        return
    
    # ========================================================================
    # AÇÕES QUE PRECISAM DE CONFIRMAÇÃO
    # ========================================================================
    
    # SILENCE - mostrar confirmação
    if action == "silence":
        await answer_callback(client, callback_id, "⚠️ Confirme a ação")
        await show_confirmation_buttons(
            client, chat_id, message_id,
            "silence", email_id, account, sender, text
        )
        return
    
    # SPAM - mostrar confirmação
    if action == "spam":
        await answer_callback(client, callback_id, "⚠️ Confirme a ação")
        await show_confirmation_buttons(
            client, chat_id, message_id,
            "spam", email_id, account, sender, text
        )
        return
    
    # SEND_DRAFT - mostrar confirmação
    if action == "send_draft":
        await answer_callback(client, callback_id, "⚠️ Confirme a ação")
        await show_confirmation_buttons(
            client, chat_id, message_id,
            "send_draft", email_id, account, sender, text
        )
        return
    
    # Callback: create_task - pedir detalhes da tarefa
    if action == "create_task":
        await answer_callback(client, callback_id, "📝 Descreva a tarefa")
        
        # Salvar estado
        save_pending_reply(email_id, {
            "chat_id": chat_id,
            "message_id": message.get("message_id"),
            "email_id": email_id,
            "account": account,
            "sender": sender,
            "original_text": text,
            "waiting_task_details": True
        })
        
        # Enviar mensagem pedindo detalhes
        await send_message(client, chat_id,
            f"📝 <b>Descreva a tarefa:</b>\n\n"
            f"Exemplos:\n"
            f"• Ligar para o cliente sobre o contrato\n"
            f"• Revisar documentação do projeto X\n"
            f"• Preparar apresentação para reunião\n\n"
            f"Deixe em branco para usar o assunto do email.",
            reply_markup={
                "inline_keyboard": [[
                    {"text": "❌ Cancelar", "callback_data": f"cancel_create_task:{email_id}"}
                ]]
            })
        return
    
    # Callback: cancel_create_task
    if action == "cancel_create_task":
        await answer_callback(client, callback_id, "❌ Cancelado")
        clear_pending_reply(email_id)
        # Apagar mensagem
        try:
            await client.post(
                f"{API_BASE}/deleteMessage",
                json={"chat_id": chat_id, "message_id": message.get("message_id")}
            )
        except Exception as e:
            logger.error(f"Erro ao apagar: {e}")
        return
    
    action_map = {
        "read": ("✅ Marcando como lido...", lambda: action_mark_read(email_id, account, client, chat_id)),
        "archive": ("🗑️ Arquivando...", lambda: action_archive(email_id, account, client, chat_id)),
        "create_task": ("📋 Criando tarefa...", lambda: action_create_task(email_id, subject, extract_urgency_from_message(text), client, chat_id)),
        "vip": ("⭐ Adicionando VIP...", lambda: action_vip(sender, client, chat_id)),
        "reclassify": ("🔄 Reclassificando...", lambda: action_reclassify_start(email_id, message, client)),
    }
    
    if action in action_map:
        emoji_text, action_func = action_map[action]
        await answer_callback(client, callback_id, emoji_text)
        await action_func()
    else:
        await answer_callback(client, callback_id, f"Ação: {action}")

# Carregar pending_actions ao iniciar
pending_actions = load_pending_actions()

# Flag de shutdown graceful
_shutdown = False


def _handle_shutdown(signum, frame):
    global _shutdown
    logger.info(f"Sinal {signum} recebido, encerrando gracefully...")
    _shutdown = True


async def main():
    """Loop principal de polling com graceful shutdown"""
    global _shutdown

    # Registrar handlers de sinal
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    logger.info("Email Agent Bot iniciado com ações reais")

    offset = 0

    async with httpx.AsyncClient(timeout=60.0) as client:
        while not _shutdown:
            try:
                updates = await get_updates(client, offset)

                for update in updates:
                    if _shutdown:
                        break
                    offset = update["update_id"] + 1

                    # Processar callback
                    if "callback_query" in update:
                        await process_callback(update["callback_query"], client)

                    # Processar mensagem normal (para responder custom)
                    elif "message" in update:
                        msg = update["message"]
                        msg_chat_id = msg.get("chat", {}).get("id")
                        msg_text = msg.get("text", "")

                        # Verificar se há custom reply pendente
                        pending = _load_json(PENDING_REPLIES_FILE, {})
                        for eid, state in pending.items():
                            if state.get("waiting_instruction") and state.get("chat_id") == msg_chat_id:
                                await action_custom_reply_generate(eid, msg_text, client)
                                break

                            if state.get("waiting_task_details") and state.get("chat_id") == msg_chat_id:
                                urgency = extract_urgency_from_message(state.get("original_text", ""))
                                subject = ""
                                for line in state.get("original_text", "").split("\n"):
                                    if "📋" in line:
                                        subject = line.replace("📋", "").strip()
                                        break
                                await action_create_task(eid, subject, urgency, msg_text, client, msg_chat_id)
                                clear_pending_reply(eid)
                                break

                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Erro no loop: {e}")
                await asyncio.sleep(5)

    # Salvar estado antes de sair
    save_pending_actions()
    logger.info("Bot encerrado.")


if __name__ == "__main__":
    asyncio.run(main())