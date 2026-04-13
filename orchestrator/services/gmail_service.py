"""
Gmail Service - Integração direta com Gmail API via google-api-python-client
Substitui o GOGService (que dependia do binário GOG CLI).
Suporta múltiplas contas com tokens OAuth separados.
"""

import os
import json
import asyncio
import logging
import base64
from email.mime.text import MIMEText
from typing import Dict, Any, List, Optional
from pathlib import Path

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

BASE_DIR = Path(os.getenv("EMAIL_AGENT_BASE_DIR", Path(__file__).resolve().parent.parent.parent))
CREDENTIALS_DIR = BASE_DIR / "credentials"
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.readonly",
]


class GmailService:
    """Serviço para interagir com Gmail via API direta (sem GOG CLI)"""

    def __init__(self):
        self._services: Dict[str, Any] = {}
        self._ready = False
        self._init_credentials()

    def _init_credentials(self):
        """Carrega tokens OAuth para todas as contas configuradas"""
        if not CREDENTIALS_DIR.exists():
            logger.warning(f"Diretório de credenciais não encontrado: {CREDENTIALS_DIR}")
            logger.info("Execute 'python scripts/gmail_auth.py' para autenticar")
            return

        token_files = list(CREDENTIALS_DIR.glob("token_*.json"))
        if not token_files:
            logger.warning("Nenhum token OAuth encontrado em credentials/")
            logger.info("Execute 'python scripts/gmail_auth.py --account seu@email.com'")
            return

        for token_file in token_files:
            # Extrair email do nome: token_user@gmail.com.json
            account = token_file.stem.replace("token_", "")
            try:
                creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                    # Salvar token atualizado
                    with open(token_file, "w") as f:
                        f.write(creds.to_json())

                if creds and creds.valid:
                    service = build("gmail", "v1", credentials=creds)
                    self._services[account] = service
                    logger.info(f"Gmail API autenticada para: {account}")
                else:
                    logger.warning(f"Token inválido para {account}, re-autentique")
            except Exception as e:
                logger.error(f"Erro ao carregar credenciais para {account}: {e}")

        self._ready = len(self._services) > 0
        if self._ready:
            logger.info(f"GmailService pronto ({len(self._services)} conta(s))")
        else:
            logger.warning("GmailService: nenhuma conta autenticada")

    def _get_service(self, account: str):
        """Retorna o serviço Gmail para a conta especificada"""
        service = self._services.get(account)
        if not service:
            logger.error(f"Conta não autenticada: {account}")
            return None
        return service

    def is_ready(self) -> bool:
        return self._ready

    # ============================================================
    # OPERAÇÕES GMAIL (async wrappers)
    # ============================================================

    async def get_email(self, email_id: str, account: str) -> Optional[Dict[str, Any]]:
        """Busca email completo pelo ID"""
        service = self._get_service(account)
        if not service:
            return None

        try:
            msg = await asyncio.to_thread(
                service.users().messages().get(
                    userId="me", id=email_id, format="full"
                ).execute
            )
            return self._parse_message(msg)
        except HttpError as e:
            logger.error(f"Erro ao buscar email {email_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"Erro inesperado ao buscar email {email_id}: {e}")
            return None

    async def get_thread(self, thread_id: str, account: str) -> List[Dict[str, Any]]:
        """Busca todos os emails de uma thread"""
        service = self._get_service(account)
        if not service:
            return []

        try:
            thread = await asyncio.to_thread(
                service.users().threads().get(
                    userId="me", id=thread_id, format="full"
                ).execute
            )
            messages = thread.get("messages", [])
            return [self._parse_message(msg) for msg in messages]
        except HttpError as e:
            logger.error(f"Erro ao buscar thread {thread_id}: {e}")
            return []
        except Exception as e:
            logger.error(f"Erro inesperado ao buscar thread {thread_id}: {e}")
            return []

    async def archive_email(self, email_id: str, account: str) -> bool:
        """Arquiva um email (remove INBOX e UNREAD)"""
        service = self._get_service(account)
        if not service:
            return False

        try:
            await asyncio.to_thread(
                service.users().messages().modify(
                    userId="me", id=email_id,
                    body={"removeLabelIds": ["INBOX", "UNREAD"]}
                ).execute
            )
            logger.info(f"Email arquivado: {email_id}")
            return True
        except HttpError as e:
            logger.error(f"Erro ao arquivar email {email_id}: {e}")
            return False

    async def mark_as_spam(self, email_id: str, account: str) -> bool:
        """Marca email como spam"""
        service = self._get_service(account)
        if not service:
            return False

        try:
            await asyncio.to_thread(
                service.users().messages().modify(
                    userId="me", id=email_id,
                    body={
                        "removeLabelIds": ["INBOX", "UNREAD"],
                        "addLabelIds": ["SPAM"]
                    }
                ).execute
            )
            logger.info(f"Email marcado como spam: {email_id}")
            return True
        except HttpError as e:
            logger.error(f"Erro ao marcar spam {email_id}: {e}")
            return False

    async def create_draft(
        self, to: str, subject: str, body: str,
        account: str, thread_id: Optional[str] = None
    ) -> Optional[str]:
        """Cria rascunho de resposta"""
        service = self._get_service(account)
        if not service:
            return None

        try:
            message = MIMEText(body)
            message["to"] = to
            message["subject"] = subject

            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
            draft_body: Dict[str, Any] = {"message": {"raw": raw}}
            if thread_id:
                draft_body["message"]["threadId"] = thread_id

            draft = await asyncio.to_thread(
                service.users().drafts().create(
                    userId="me", body=draft_body
                ).execute
            )
            draft_id = draft.get("id", "")
            logger.info(f"Rascunho criado: {draft_id}")
            return draft_id
        except HttpError as e:
            logger.error(f"Erro ao criar rascunho: {e}")
            return None

    async def send_reply(
        self, email_id: str, body: str, account: str,
        to: Optional[str] = None, subject: Optional[str] = None,
        thread_id: Optional[str] = None
    ) -> bool:
        """Envia resposta a um email"""
        service = self._get_service(account)
        if not service:
            return False

        try:
            # Se não temos to/subject, buscar do email original
            if not to or not subject:
                original = await self.get_email(email_id, account)
                if original:
                    to = to or original.get("from", "")
                    subject = subject or f"Re: {original.get('subject', '')}"
                    thread_id = thread_id or original.get("threadId", "")

            if not to:
                logger.error("Não foi possível determinar o destinatário")
                return False

            message = MIMEText(body)
            message["to"] = to
            message["subject"] = subject or "Re:"
            message["In-Reply-To"] = email_id
            message["References"] = email_id

            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
            send_body: Dict[str, Any] = {"raw": raw}
            if thread_id:
                send_body["threadId"] = thread_id

            await asyncio.to_thread(
                service.users().messages().send(
                    userId="me", body=send_body
                ).execute
            )
            logger.info(f"Resposta enviada para {to}")
            return True
        except HttpError as e:
            logger.error(f"Erro ao enviar resposta: {e}")
            return False

    async def get_history(self, history_id: str, account: str) -> List[str]:
        """Busca IDs de mensagens desde um historyId"""
        service = self._get_service(account)
        if not service:
            return []

        try:
            response = await asyncio.to_thread(
                service.users().history().list(
                    userId="me",
                    startHistoryId=history_id,
                    historyTypes=["messageAdded"],
                    labelId="INBOX"
                ).execute
            )

            message_ids = []
            for record in response.get("history", []):
                for msg_added in record.get("messagesAdded", []):
                    msg = msg_added.get("message", {})
                    msg_id = msg.get("id")
                    if msg_id:
                        message_ids.append(msg_id)

            return message_ids
        except HttpError as e:
            if e.resp.status == 404:
                logger.warning(f"historyId {history_id} expirado")
            else:
                logger.error(f"Erro ao buscar history: {e}")
            return []
        except Exception as e:
            logger.error(f"Erro inesperado ao buscar history: {e}")
            return []

    async def watch(self, account: str, topic: str, label_ids: List[str] = None) -> Optional[Dict]:
        """Ativa Gmail Watch (Pub/Sub push notifications). Expira em 7 dias."""
        service = self._get_service(account)
        if not service:
            return None

        if label_ids is None:
            label_ids = ["INBOX"]

        try:
            response = await asyncio.to_thread(
                service.users().watch(
                    userId="me",
                    body={
                        "topicName": topic,
                        "labelIds": label_ids,
                        "labelFilterBehavior": "INCLUDE"
                    }
                ).execute
            )
            logger.info(f"Gmail Watch ativado para {account}: expira em {response.get('expiration')}")
            return response
        except HttpError as e:
            logger.error(f"Erro ao ativar Gmail Watch para {account}: {e}")
            return None

    async def move_to_label(self, email_id: str, label: str, account: str) -> bool:
        """Move email para uma label específica"""
        service = self._get_service(account)
        if not service:
            return False

        try:
            await asyncio.to_thread(
                service.users().messages().modify(
                    userId="me", id=email_id,
                    body={"addLabelIds": [label]}
                ).execute
            )
            return True
        except HttpError as e:
            logger.error(f"Erro ao mover email {email_id} para {label}: {e}")
            return False

    async def get_attachment(self, email_id: str, attachment_id: str, account: str) -> Optional[bytes]:
        """Download attachment bytes by ID."""
        service = self._get_service(account)
        if not service:
            return None
        try:
            result = await asyncio.to_thread(
                service.users().messages().attachments().get(
                    userId="me", messageId=email_id, id=attachment_id
                ).execute
            )
            data = result.get("data", "")
            return base64.urlsafe_b64decode(data) if data else None
        except Exception as e:
            logger.error(f"Error fetching attachment {attachment_id}: {e}")
            return None

    # ============================================================
    # PARSING
    # ============================================================

    def _parse_message(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """Converte resposta da Gmail API para formato padronizado"""
        result = {
            "id": msg.get("id", ""),
            "threadId": msg.get("threadId", ""),
            "subject": "",
            "from": "",
            "from_name": "",
            "from_email": "",
            "to": "",
            "cc": "",
            "date": "",
            "body": "",
            "body_clean": "",
            "attachments": [],
            "labels": msg.get("labelIds", [])
        }

        # Extrair headers (Gmail API retorna lista de {name, value})
        payload = msg.get("payload", {})
        headers = payload.get("headers", [])

        if isinstance(headers, list):
            for h in headers:
                name = h.get("name", "").lower()
                value = h.get("value", "")
                if name == "subject":
                    result["subject"] = value
                elif name == "from":
                    result["from"] = value
                    result["from_name"], result["from_email"] = self._parse_from(value)
                elif name == "to":
                    result["to"] = value
                elif name == "cc":
                    result["cc"] = value
                elif name == "date":
                    result["date"] = value

        # Extrair body
        result["body"] = self._extract_body(payload)

        result["attachments"] = self._extract_attachments(payload)

        return result

    def _extract_attachments(self, payload: dict) -> list:
        """Extract attachment metadata from email payload."""
        attachments = []
        parts = payload.get("parts", [])
        for part in parts:
            filename = part.get("filename", "")
            body = part.get("body", {})
            attachment_id = body.get("attachmentId")
            if filename and attachment_id:
                attachments.append({
                    "filename": filename,
                    "mimeType": part.get("mimeType", ""),
                    "size": body.get("size", 0),
                    "attachmentId": attachment_id,
                })
            # Check nested parts
            if part.get("parts"):
                for nested in part["parts"]:
                    fn = nested.get("filename", "")
                    nb = nested.get("body", {})
                    aid = nb.get("attachmentId")
                    if fn and aid:
                        attachments.append({
                            "filename": fn,
                            "mimeType": nested.get("mimeType", ""),
                            "size": nb.get("size", 0),
                            "attachmentId": aid,
                        })
        return attachments

    def _extract_body(self, payload: Dict[str, Any]) -> str:
        """Extrai corpo do email (text/plain ou text/html)"""
        mime_type = payload.get("mimeType", "")

        # Corpo direto no payload
        body_data = payload.get("body", {}).get("data", "")
        if body_data:
            decoded = base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")
            if "text/html" in mime_type:
                return self._html_to_text(decoded)
            return decoded

        # Multipart: buscar nas parts
        parts = payload.get("parts", [])
        text_body = ""
        html_body = ""

        for part in parts:
            part_mime = part.get("mimeType", "")
            part_data = part.get("body", {}).get("data", "")

            if part_data:
                decoded = base64.urlsafe_b64decode(part_data).decode("utf-8", errors="replace")
                if part_mime == "text/plain":
                    text_body = decoded
                elif part_mime == "text/html":
                    html_body = decoded
            elif part.get("parts"):
                # Nested multipart
                nested = self._extract_body(part)
                if nested:
                    text_body = text_body or nested

        # Preferir text/plain, fallback para HTML convertido
        if text_body:
            return text_body
        if html_body:
            return self._html_to_text(html_body)

        return ""

    def _parse_from(self, from_header: str) -> tuple:
        """Parse do header From para separar nome e email"""
        import re
        if not from_header:
            return "", ""

        match = re.search(r'([^<]+)<([^>]+)>', from_header)
        if match:
            name = match.group(1).strip().strip('"')
            email_addr = match.group(2).strip()
            return name, email_addr

        if "@" in from_header:
            return "", from_header.strip()

        return from_header.strip(), ""

    def _html_to_text(self, html_content: str) -> str:
        """Converte HTML para texto limpo"""
        import re
        import html

        # Remover scripts e styles
        html_content = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
        html_content = re.sub(r'<style[^>]*>.*?</style>', '', html_content, flags=re.DOTALL | re.IGNORECASE)

        # Converter quebras
        html_content = re.sub(r'<br\s*/?>', '\n', html_content, flags=re.IGNORECASE)
        html_content = re.sub(r'</p>', '\n\n', html_content, flags=re.IGNORECASE)
        html_content = re.sub(r'</div>', '\n', html_content, flags=re.IGNORECASE)
        html_content = re.sub(r'<li[^>]*>', '\n• ', html_content, flags=re.IGNORECASE)

        # Remover tags
        html_content = re.sub(r'<[^>]+>', '', html_content)

        # Decodar entidades
        html_content = html.unescape(html_content)

        # Limpar espaços
        html_content = re.sub(r'\n{3,}', '\n\n', html_content)
        html_content = re.sub(r' {2,}', ' ', html_content)

        if len(html_content) > 3000:
            html_content = html_content[:3000] + "\n... [truncado]"

        return html_content.strip()
