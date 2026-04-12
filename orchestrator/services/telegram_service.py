"""
Telegram Service - Mensagens formatadas e botões inline
"""

import os
import logging
import html
from typing import Dict, Any, Optional
import httpx

logger = logging.getLogger(__name__)


class TelegramService:
    """Serviço para enviar notificações formatadas no Telegram"""
    
    # Emojis de urgência
    URGENCY_EMOJI = {
        "critical": "🔴",
        "high": "🟠",
        "medium": "🟡",
        "low": "🟢"
    }
    
    # Emojis de categoria
    CATEGORY_EMOJI = {
        "financeiro": "💰",
        "cliente": "👤",
        "infra": "🖥️",
        "dev": "💻",
        "pessoal": "🏠",
        "newsletter": "📰",
        "spam": "🗑️",
        "outro": "📧"
    }
    
    def __init__(self):
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.api_base = f"https://api.telegram.org/bot{self.bot_token}"
        self._configured = bool(self.bot_token)
        
        if self._configured:
            logger.info("TelegramService configurado")
    
    # Limite do Telegram para mensagens
    MAX_MESSAGE_LENGTH = 4096

    async def send_email_notification(
        self,
        email: Dict[str, Any],
        classification: Dict[str, Any],
        summary: Dict[str, Any],
        action: Dict[str, Any],
        topic_id: Optional[int] = 11,
        reasoning_tokens: int = 0
    ) -> Optional[int]:
        """Envia notificação formatada. Mensagens longas são divididas."""

        if not self._configured:
            logger.warning("Telegram não configurado")
            return None

        text = self._format_message(email, classification, summary, action, reasoning_tokens)
        reply_markup = self._create_keyboard(email, action)

        # Se mensagem cabe no limite, enviar normalmente
        if len(text) <= self.MAX_MESSAGE_LENGTH:
            return await self._send_message(text, topic_id, reply_markup)

        # Mensagem longa: dividir em partes, botões só na última
        parts = self._split_message(text)
        last_message_id = None
        for i, part in enumerate(parts):
            is_last = (i == len(parts) - 1)
            markup = reply_markup if is_last else None
            last_message_id = await self._send_message(part, topic_id, markup)

        return last_message_id

    async def _send_message(
        self, text: str, topic_id: Optional[int] = None,
        reply_markup: Optional[Dict] = None
    ) -> Optional[int]:
        """Envia uma mensagem individual ao Telegram"""
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        if topic_id:
            payload["message_thread_id"] = topic_id
        if reply_markup:
            payload["reply_markup"] = reply_markup

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.api_base}/sendMessage", json=payload
                )
                if response.status_code == 200:
                    data = response.json()
                    msg_id = data.get("result", {}).get("message_id")
                    logger.info(f"Notificação enviada: message_id={msg_id}")
                    return msg_id
                else:
                    logger.error(f"Erro Telegram: {response.status_code} - {response.text}")
                    return None
        except Exception as e:
            logger.error(f"Erro ao enviar: {e}")
            return None

    def _split_message(self, text: str) -> list:
        """Divide mensagem longa em partes respeitando o limite do Telegram.
        Tenta quebrar em linhas vazias para não cortar no meio de um parágrafo."""
        limit = self.MAX_MESSAGE_LENGTH - 20  # margem de segurança
        if len(text) <= limit:
            return [text]

        parts = []
        while len(text) > limit:
            # Tentar quebrar em linha vazia antes do limite
            split_at = text.rfind("\n\n", 0, limit)
            if split_at == -1:
                split_at = text.rfind("\n", 0, limit)
            if split_at == -1:
                split_at = limit

            parts.append(text[:split_at])
            text = text[split_at:].lstrip("\n")

        if text:
            parts.append(text)

        return parts
    
    def _format_message(
        self,
        email: Dict[str, Any],
        classification: Dict[str, Any],
        summary: Dict[str, Any],
        action: Dict[str, Any],
        reasoning_tokens: int = 0
    ) -> str:
        """Formata mensagem seguindo o padrão do guia"""
        
        # Urgência e categoria
        prioridade = classification.get("prioridade", "Média").lower()
        importante = classification.get("importante", False)
        
        # Mapear prioridade para urgência
        if importante and prioridade in ["alta", "high"]:
            urgencia_key = "critical"
        elif prioridade in ["alta", "high"]:
            urgencia_key = "high"
        elif prioridade in ["média", "medium"]:
            urgencia_key = "medium"
        else:
            urgencia_key = "low"
        
        urgencia_emoji = self.URGENCY_EMOJI.get(urgencia_key, "📧")
        urgencia_text = urgencia_key.upper()
        
        categoria = classification.get("categoria", "outro").lower()
        categoria_emoji = self.CATEGORY_EMOJI.get(categoria, "📧")
        categoria_text = categoria.capitalize()
        
        confianca = int(classification.get("confianca", 0.5) * 100)
        
        # Remetente (nome amigável)
        from_name = email.get("from_name", "") or email.get("from", "Desconhecido")
        if "<" in from_name:
            from_name = from_name.split("<")[0].strip().strip('"')
        
        # Assunto limpo
        subject = email.get("subject", "Sem assunto")
        
        # Resumo (máx 2 frases)
        resumo = summary.get("resumo", "Sem resumo")
        if len(resumo) > 400:
            resumo = resumo[:400] + "..."
        
        # Rascunho (formato do guia)
        rascunho = action.get("rascunho_resposta", "")
        
        # Data/hora
        date_str = email.get("date", "")
        
        # Montar mensagem
        lines = [
            f"<b>{urgencia_emoji} {urgencia_text} │ {categoria_emoji} {categoria_text} │ {confianca}%</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"📨 {html.escape(from_name)}",
            f"📋 {html.escape(subject)}",
            "",
            f"📝 {html.escape(resumo)}",
        ]
        
        # Ação necessária (só para critical/high)
        if urgencia_key in ["critical", "high"]:
            acao_text = action.get("justificativa", "Verificar e tomar ação necessária.")
            lines.append("")
            lines.append("⚠️ <b>AÇÃO NECESSÁRIA</b>")
            lines.append(html.escape(acao_text[:300]))
        
        # Rascunho (formato do guia - sem borda)
        if rascunho:
            lines.append("")
            lines.append("💬 <b>RASCUNHO PRONTO:</b>")
            # Dividir rascunho em linhas
            rascunho_lines = rascunho.split("\n")
            for line in rascunho_lines[:20]:  # Limite de 20 linhas
                lines.append(html.escape(line[:50]))
        
        # Rodapé
        lines.append("")
        if date_str:
            # Converter reasoning_tokens para formato legível
            if reasoning_tokens > 1000:
                reasoning_str = f"{reasoning_tokens / 1000:.1f}k"
            else:
                reasoning_str = str(reasoning_tokens)
            lines.append(f"🕐 {date_str[:16]} │ ⚙️ reasoning: {reasoning_str} tokens")
        
        return "\n".join(lines)
    
    def _create_keyboard(self, email: Dict[str, Any], action: Dict[str, Any]) -> Dict:
        """Cria teclado inline com todos os botões"""
        email_id = email.get("id", "")
        account = action.get("account", "diogenes.mendes01@gmail.com")
        sender = email.get("from_email", "") or email.get("from", "")
        
        # Callback data simplificado: action:email_id:account (sem truncar)
        # Sender será extraído da mensagem original quando necessário
        keyboard = [
            [
                {"text": "✉️ Enviar rascunho", "callback_data": f"send_draft:{email_id}:{account}"},
                {"text": "📝 Criar tarefa", "callback_data": f"create_task:{email_id}:{account}"}
            ],
            [
                {"text": "✅ Arquivar", "callback_data": f"archive:{email_id}:{account}"},
                {"text": "⭐ Marcar VIP", "callback_data": f"vip:{email_id}:{account}"}
            ],
            [
                {"text": "💬 Responder custom", "callback_data": f"custom_reply:{email_id}:{account}"},
                {"text": "🔄 Reclassificar", "callback_data": f"reclassify:{email_id}:{account}"}
            ],
            [
                {"text": "🔇 Silenciar", "callback_data": f"silence:{email_id}:{account}"},
                {"text": "🗑️ Spam", "callback_data": f"spam:{email_id}:{account}"}
            ],
            [
                {"text": "🔗 Abrir no Gmail", 
                 "url": f"https://mail.google.com/mail/u/0/#inbox/{email_id}"}
            ]
        ]
        
        return {"inline_keyboard": keyboard}
    
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
            "parse_mode": "HTML"
        }
        
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.api_base}/sendMessage",
                    json=payload
                )
                if response.status_code == 200:
                    return response.json().get("result", {}).get("message_id")
        except Exception as e:
            logger.error(f"Erro ao enviar confirmação: {e}")
        return None
    
    async def edit_message(
        self,
        message_id: int,
        text: str,
        chat_id: Optional[str] = None,
        reply_markup: Optional[Dict] = None
    ) -> bool:
        """
        Edita texto de uma mensagem existente.
        
        Args:
            message_id: ID da mensagem a editar
            text: Novo texto da mensagem
            chat_id: Chat ID (usa default se não informado)
            reply_markup: Novos botões inline (opcional)
        
        Returns:
            True se editou com sucesso, False caso contrário
        """
        if not self._configured:
            logger.warning("Telegram não configurado")
            return False
        
        chat_id = chat_id or self.chat_id
        
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        
        if reply_markup:
            payload["reply_markup"] = reply_markup
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.api_base}/editMessageText",
                    json=payload
                )
                
                if response.status_code == 200:
                    logger.info(f"Mensagem {message_id} editada com sucesso")
                    return True
                else:
                    logger.error(f"Erro ao editar mensagem: {response.status_code} - {response.text}")
                    return False
        except Exception as e:
            logger.error(f"Exceção ao editar mensagem: {e}")
            return False
    
    async def update_message_status(
        self,
        message_id: int,
        status: str,
        original_text: str,
        chat_id: Optional[str] = None
    ) -> bool:
        """
        Adiciona status ao final de uma mensagem.
        
        Args:
            message_id: ID da mensagem a atualizar
            status: Texto do status (ex: "✅ Respondido em 10/04 às 00:30")
            original_text: Texto original da mensagem
            chat_id: Chat ID (usa default se não informado)
        
        Returns:
            True se atualizou com sucesso, False caso contrário
        """
        # Remove status anterior se existir
        if "\n\n<b>━━━ STATUS ━━━</b>" in original_text:
            original_text = original_text.split("\n\n<b>━━━ STATUS ━━━</b>")[0]
        
        # Adiciona novo status
        new_text = f"{original_text}\n\n<b>━━━ STATUS ━━━</b>\n{html.escape(status)}"
        
        return await self.edit_message(message_id, new_text, chat_id)
    
    async def disable_buttons(
        self,
        message_id: int,
        chat_id: Optional[str] = None
    ) -> bool:
        """
        Remove botões inline de uma mensagem.
        
        Args:
            message_id: ID da mensagem
            chat_id: Chat ID (usa default se não informado)
        
        Returns:
            True se removeu com sucesso, False caso contrário
        """
        if not self._configured:
            logger.warning("Telegram não configurado")
            return False
        
        chat_id = chat_id or self.chat_id
        
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": {"inline_keyboard": []}
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.api_base}/editMessageReplyMarkup",
                    json=payload
                )
                
                if response.status_code == 200:
                    logger.info(f"Botões removidos da mensagem {message_id}")
                    return True
                else:
                    logger.error(f"Erro ao remover botões: {response.status_code} - {response.text}")
                    return False
        except Exception as e:
            logger.error(f"Exceção ao remover botões: {e}")
            return False