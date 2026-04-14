"""Telegram callback router — dispatches callbacks to action modules."""
import json
import logging
import re
from datetime import datetime

from orchestrator.actions import archive, vip, silence, spam, task
from orchestrator.actions import feedback, reply

logger = logging.getLogger(__name__)

# Actions that require a confirmation step before execution.
# Note: send_draft confirms the LLM-generated draft from the email pipeline,
# while send_custom_draft (from custom_reply flow) has its own handler above.
CONFIRM_ACTIONS = {"archive", "vip", "silence", "spam", "send_draft"}

# Loading indicators per action type
_ACTION_LOADING = {
    "archive": "📦 Arquivando...",
    "vip": "⭐ Adicionando VIP...",
    "silence": "🔇 Silenciando...",
    "spam": "🗑️ Marcando spam...",
    "send_draft": "✉️ Enviando rascunho...",
}


def _extract_sender(text: str) -> str:
    """Extract sender email from message text."""
    for line in text.split("\n"):
        if "📨" in line:
            sender = line.replace("📨", "").strip()
            match = re.search(r"<([^>]+)>", sender)
            if match:
                return match.group(1)
            return sender
    return ""


def _extract_subject(text: str) -> str:
    """Extract subject from message text."""
    for line in text.split("\n"):
        if "📋" in line:
            return line.replace("📋", "").strip()
    return ""


def _extract_urgency(text: str) -> str:
    """Extract urgency from message text."""
    text_lower = text.lower()
    for key in ("critical", "high", "medium", "low"):
        if key in text_lower:
            return key
    return "medium"


def _build_original_keyboard(email_id: str, account: str, auto_responded: bool = False) -> dict:
    """Rebuild the original inline keyboard.

    When auto_responded=True, reply/draft buttons are omitted to match the
    keyboard that was originally shown for auto-responded emails.
    """
    keyboard = []
    if not auto_responded:
        keyboard.append([
            {"text": "✉️ Enviar rascunho", "callback_data": f"send_draft:{email_id}:{account}"},
            {"text": "📝 Criar tarefa", "callback_data": f"create_task:{email_id}:{account}"},
        ])
        keyboard.append([
            {"text": "✅ Arquivar", "callback_data": f"archive:{email_id}:{account}"},
            {"text": "⭐ Marcar VIP", "callback_data": f"vip:{email_id}:{account}"},
        ])
        keyboard.append([
            {"text": "💬 Responder custom", "callback_data": f"custom_reply:{email_id}:{account}"},
            {"text": "🔄 Reclassificar", "callback_data": f"reclassify:{email_id}:{account}"},
        ])
        keyboard.append([
            {"text": "🔇 Silenciar", "callback_data": f"silence:{email_id}:{account}"},
            {"text": "🗑️ Spam", "callback_data": f"spam:{email_id}:{account}"},
        ])
    else:
        keyboard.append([
            {"text": "✅ Arquivar", "callback_data": f"archive:{email_id}:{account}"},
            {"text": "📝 Criar tarefa", "callback_data": f"create_task:{email_id}:{account}"},
        ])
        keyboard.append([
            {"text": "⭐ Marcar VIP", "callback_data": f"vip:{email_id}:{account}"},
            {"text": "🔄 Reclassificar", "callback_data": f"reclassify:{email_id}:{account}"},
        ])
    keyboard.append([
        {"text": "🔗 Abrir no Gmail", "url": f"https://mail.google.com/mail/u/0/#inbox/{email_id}"},
    ])
    return {"inline_keyboard": keyboard}


def _confirmation_text(action: str, sender: str) -> str:
    """Build confirmation message text."""
    titles = {
        "archive": "✅ <b>Arquivar Email</b>",
        "vip": "⭐ <b>Marcar como VIP</b>",
        "silence": "🔇 <b>Silenciar Remetente</b>",
        "spam": "🚫 <b>Marcar como Spam</b>",
        "send_draft": "✉️ <b>Enviar Rascunho</b>",
    }
    warnings = {
        "archive": "Este email será arquivado no Gmail.",
        "vip": f"O remetente <b>{sender}</b> será adicionado à lista VIP.",
        "silence": "Este remetente será adicionado à blacklist.",
        "spam": "Este email será marcado como spam + blacklist.",
        "send_draft": "O rascunho será enviado como resposta.",
    }
    return (
        f"{titles.get(action, '⚠️ Confirmar Ação')}\n\n"
        f"📧 Remetente: {sender}\n\n"
        f"⚠️ {warnings.get(action, '')}\n\n"
        f"<b>Confirma esta ação?</b>"
    )


async def handle_callback(callback_query: dict, services: dict):
    """Main callback router. Parses callback data and dispatches to actions.

    Args:
        callback_query: The callback_query object from Telegram update.
        services: Dict with keys 'db', 'gmail', 'telegram', 'llm'.
    """
    callback_id = callback_query["id"]
    callback_data = callback_query.get("data", "")
    actor_id = callback_query.get("from", {}).get("id", 0)
    message = callback_query.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    topic_id = message.get("message_thread_id")
    message_id = message.get("message_id")
    text = message.get("text", "")

    tg = services["telegram"]
    db = services["db"]

    # Auth: only allowed users can trigger actions
    allowed = services.get("allowed_user_ids", set())
    if allowed and actor_id not in allowed:
        logger.warning(f"Unauthorized callback from user {actor_id}")
        await tg.answer_callback(callback_id, "⛔ Acesso não autorizado")
        return

    parts = callback_data.split(":")
    action = parts[0] if parts else "unknown"

    logger.info(f"Callback: {action} | data={callback_data[:60]}")

    # --- SET URGENCY (reclassify step 2) ---
    if action == "set_urgency":
        new_urgency = parts[1] if len(parts) > 1 else "medium"
        email_id = parts[2] if len(parts) > 2 else ""
        await tg.answer_callback(callback_id, f"✅ {new_urgency.upper()}")
        pending = await db.get_pending_action(email_id, "reclassify", actor_id=actor_id, topic_id=topic_id)
        if pending:
            ctx = _build_ctx(email_id, "", actor_id, chat_id, message_id, text, services, pending=pending, topic_id=topic_id)
            ctx["new_urgency"] = new_urgency
            status = await feedback.complete_reclassify(ctx)
            state = json.loads(pending["state"]) if isinstance(pending["state"], str) else pending["state"]
            original_text = state.get("original_text", text)
            await _mark_done(tg, chat_id, pending.get("message_id", message_id), status, original_text)
        return

    # --- CANCEL RECLASSIFY ---
    if action == "cancel_reclassify":
        email_id = parts[1] if len(parts) > 1 else ""
        await tg.answer_callback(callback_id, "❌ Cancelado")
        pending = await db.get_pending_action(email_id, "reclassify", actor_id=actor_id, topic_id=topic_id)
        if pending:
            state = json.loads(pending["state"]) if isinstance(pending["state"], str) else pending["state"]
            account = state.get("account", "")
            original_text = state.get("original_text", text)
            was_auto = "Auto-respondido via playbook" in original_text
            await tg.edit_reply_markup(chat_id, message_id, _build_original_keyboard(email_id, account, auto_responded=was_auto))
            await db.delete_pending_action(pending["id"])
        return

    # --- CANCEL CUSTOM REPLY ---
    if action == "cancel_custom_reply":
        email_id = parts[1] if len(parts) > 1 else ""
        await tg.answer_callback(callback_id, "❌ Cancelado")
        pending = await db.get_pending_action(email_id, "custom_reply", actor_id=actor_id, topic_id=topic_id)
        if pending:
            await db.delete_pending_action(pending["id"])
        await tg.delete_message(chat_id, message_id)
        return

    # --- SEND CUSTOM DRAFT ---
    if action == "send_custom_draft":
        email_id = parts[1] if len(parts) > 1 else ""
        await tg.answer_callback(callback_id, "✉️ Enviando...")
        pending = await db.get_pending_action(email_id, "custom_reply", actor_id=actor_id, topic_id=topic_id)
        if pending:
            state = json.loads(pending["state"]) if isinstance(pending["state"], str) else pending["state"]
            original_text = state.get("original_text", text)
            # Show loading indicator immediately
            loading_text = f"{original_text}\n\n───\n✉️ Enviando rascunho..."
            await tg.edit_message(
                pending.get("message_id", message_id), loading_text,
                chat_id=str(chat_id), reply_markup={"inline_keyboard": []}
            )
            ctx = _build_ctx(email_id, "", actor_id, chat_id, message_id, text, services, pending=pending, topic_id=topic_id)
            status = await reply.send_draft(ctx)
            await _mark_done(tg, chat_id, pending.get("message_id", message_id), status, original_text)
        return

    # --- ADJUST CUSTOM DRAFT ---
    if action == "adjust_custom_draft":
        email_id = parts[1] if len(parts) > 1 else ""
        await tg.answer_callback(callback_id, "✏️ Digite nova instrução")
        pending = await db.get_pending_action(email_id, "custom_reply", actor_id=actor_id, topic_id=topic_id)
        if pending:
            state = json.loads(pending["state"]) if isinstance(pending["state"], str) else pending["state"]
            state["waiting_instruction"] = True
            await db.update_pending_state(pending["id"], state)
            await tg.send_text(chat_id, "💬 <b>Digite a nova instrução:</b>", thread_id=topic_id)
        return

    # --- Parse standard callback: action:email_id:account ---
    email_id = parts[1] if len(parts) > 1 else ""
    account = parts[2] if len(parts) > 2 else ""

    # --- CONFIRM actions (second step) ---
    if action.startswith("confirm_"):
        real_action = action.replace("confirm_", "")
        await tg.answer_callback(callback_id, "✅ Executando...")
        pending = await db.get_pending_action(email_id, real_action, actor_id=actor_id, topic_id=topic_id)
        if not pending:
            return
        state = json.loads(pending["state"]) if isinstance(pending["state"], str) else pending["state"]
        original_text = state.get("original_text", text)
        # Show loading indicator immediately
        loading_msg = _ACTION_LOADING.get(real_action, "⏳ Processando...")
        loading_text = f"{original_text}\n\n───\n{loading_msg}"
        await tg.edit_message(
            pending.get("message_id", message_id), loading_text,
            chat_id=str(chat_id), reply_markup={"inline_keyboard": []}
        )
        ctx = _build_ctx(email_id, account, actor_id, chat_id, message_id, text, services, pending=pending, topic_id=topic_id)
        action_fn = _get_action_fn(real_action)
        if action_fn:
            status = await action_fn(ctx)
        else:
            status = "❌ Ação desconhecida"
        await _mark_done(tg, chat_id, pending.get("message_id", message_id), status, original_text)
        await db.delete_pending_action(pending["id"])
        return

    # --- CANCEL actions (second step) ---
    if action.startswith("cancel_"):
        real_action = action.replace("cancel_", "")
        await tg.answer_callback(callback_id, "❌ Cancelado")
        pending = await db.get_pending_action(email_id, real_action, actor_id=actor_id, topic_id=topic_id)
        if pending:
            state = json.loads(pending["state"]) if isinstance(pending["state"], str) else pending["state"]
            original_text = state.get("original_text", text)
            cancel_account = state.get("account", account)
            was_auto = "Auto-respondido via playbook" in original_text
            await tg.edit_message(message_id, original_text, chat_id=str(chat_id),
                                  reply_markup=_build_original_keyboard(email_id, cancel_account, auto_responded=was_auto))
            await db.delete_pending_action(pending["id"])
        return

    # --- RECLASSIFY (first step) ---
    if action == "reclassify":
        await tg.answer_callback(callback_id, "🔄 Selecione a urgência")
        account_data = await db.get_account(account)
        ctx = _build_ctx(email_id, account, actor_id, chat_id, message_id, text, services, topic_id=topic_id)
        if account_data:
            ctx["account_id"] = account_data["id"]
        await feedback.start_reclassify(ctx)
        return

    # --- CUSTOM REPLY (first step) ---
    if action == "custom_reply":
        await tg.answer_callback(callback_id, "💬 Aguardando instrução")
        account_data = await db.get_account(account)
        ctx = _build_ctx(email_id, account, actor_id, chat_id, message_id, text, services, topic_id=topic_id)
        if account_data:
            ctx["account_id"] = account_data["id"]
        await reply.start_reply(ctx)
        return

    # --- CREATE TASK (first step — prompts for details) ---
    if action == "create_task":
        await tg.answer_callback(callback_id, "📝 Descreva a tarefa")
        account_data = await db.get_account(account)
        keyboard = {"inline_keyboard": [[
            {"text": "❌ Cancelar", "callback_data": f"cancel_create_task:{email_id}"}
        ]]}
        prompt_msg_id = await tg.send_text(
            chat_id,
            "📝 <b>Descreva a tarefa:</b>\n\nExemplos:\n• Ligar para o cliente\n• Revisar documentação\n\nDeixe em branco para usar o assunto.",
            reply_markup=keyboard,
            thread_id=topic_id,
        )
        state = {
            "original_text": text,
            "account": account,
            "sender": _extract_sender(text),
            "subject": _extract_subject(text),
            "urgency": _extract_urgency(text),
            "prompt_msg_id": prompt_msg_id,
        }
        acct_id = account_data["id"] if account_data else None
        await db.create_pending_action(acct_id, email_id, "create_task", actor_id, chat_id, message_id, state, topic_id=topic_id)
        return

    # --- CANCEL CREATE TASK ---
    if action == "cancel_create_task":
        await tg.answer_callback(callback_id, "❌ Cancelado")
        pending = await db.get_pending_action(email_id, "create_task", actor_id=actor_id, topic_id=topic_id)
        if pending:
            await db.delete_pending_action(pending["id"])
        await tg.delete_message(chat_id, message_id)
        return

    # --- ACTIONS REQUIRING CONFIRMATION (first step) ---
    if action in CONFIRM_ACTIONS:
        await tg.answer_callback(callback_id, "⚠️ Confirme a ação")
        sender = _extract_sender(text)
        account_data = await db.get_account(account)
        acct_id = account_data["id"] if account_data else None
        state = {"original_text": text, "account": account, "sender": sender}
        await db.create_pending_action(acct_id, email_id, action, actor_id, chat_id, message_id, state, topic_id=topic_id)
        confirm_keyboard = {
            "inline_keyboard": [[
                {"text": "✅ Confirmar", "callback_data": f"confirm_{action}:{email_id}:{account}"},
                {"text": "❌ Cancelar", "callback_data": f"cancel_{action}:{email_id}:{account}"},
            ]]
        }
        await tg.edit_message(message_id, _confirmation_text(action, sender), chat_id=str(chat_id),
                              reply_markup=confirm_keyboard)
        return

    # --- UNKNOWN ---
    await tg.answer_callback(callback_id, f"Ação desconhecida: {action}")
    logger.warning(f"Unknown callback action: {action}")


async def handle_text_message(message: dict, services: dict):
    """Handle text messages (for config commands, custom reply instructions, task details).

    Args:
        message: The message object from Telegram update.
        services: Dict with keys 'db', 'gmail', 'telegram', 'llm'.
    """
    chat_id = message.get("chat", {}).get("id")
    topic_id = message.get("message_thread_id")
    actor_id = message.get("from", {}).get("id", 0)
    text = message.get("text", "")
    db = services["db"]
    tg = services["telegram"]
    llm = services["llm"]

    # Auth: only allowed users can send commands/messages
    allowed = services.get("allowed_user_ids", set())
    if allowed and actor_id not in allowed:
        logger.warning(f"Unauthorized message from user {actor_id}")
        return

    # Check if it's a /config command
    from orchestrator.handlers.telegram_commands import is_command, handle_command, handle_config_response
    if is_command(text):
        await handle_command(message, services)
        return

    # Check for pending config conversation
    for config_type in ("config_identidade", "config_playbook"):
        pending = await db.get_pending_by_chat(chat_id, config_type, actor_id=actor_id, topic_id=topic_id)
        if pending:
            await handle_config_response(message, pending, services)
            return

    # Check for pending custom_reply waiting for instruction
    pending = await db.get_pending_by_chat(chat_id, "custom_reply", actor_id=actor_id, topic_id=topic_id)
    if pending:
        state = json.loads(pending["state"]) if isinstance(pending["state"], str) else pending["state"]
        if state.get("waiting_instruction", True):
            ctx = {
                "email_id": pending["email_id"],
                "account": state.get("account", ""),
                "chat_id": chat_id,
                "topic_id": topic_id,
                "instruction": text,
                "pending": pending,
                "db": db,
                "gmail": services["gmail"],
                "telegram": tg,
                "llm": llm,
            }
            await reply.generate_reply(ctx)
            return

    # Check for pending create_task waiting for details
    pending = await db.get_pending_by_chat(chat_id, "create_task", actor_id=actor_id, topic_id=topic_id)
    if pending:
        state = json.loads(pending["state"]) if isinstance(pending["state"], str) else pending["state"]
        user_msg_id = message.get("message_id")
        prompt_msg_id = state.get("prompt_msg_id")
        ctx = {
            "email_id": pending["email_id"],
            "account": state.get("account", ""),
            "account_id": pending.get("account_id"),
            "chat_id": chat_id,
            "task_details": text,
            "subject": state.get("subject", ""),
            "urgency": state.get("urgency", "medium"),
            "db": db,
            "gmail": services["gmail"],
            "telegram": tg,
            "llm": llm,
        }
        status = await task.execute(ctx)

        # Edit the prompt message with the result instead of sending a new message
        if prompt_msg_id:
            await tg.edit_message(prompt_msg_id, status, chat_id=chat_id)
        else:
            await tg.send_text(chat_id, status, thread_id=topic_id)

        # Delete the user's message to keep the chat clean
        if user_msg_id:
            await tg.delete_message(chat_id, user_msg_id)

        await db.delete_pending_action(pending["id"])
        return


def _build_ctx(email_id, account, actor_id, chat_id, message_id, text, services, pending=None, topic_id=None):
    """Build action context dict."""
    state = {}
    if pending:
        state = json.loads(pending["state"]) if isinstance(pending["state"], str) else pending["state"]
    return {
        "email_id": email_id,
        "account": state.get("account", account),
        "account_id": pending.get("account_id") if pending else None,
        "sender": state.get("sender", _extract_sender(text)),
        "subject": _extract_subject(text),
        "urgency": _extract_urgency(text),
        "chat_id": chat_id,
        "topic_id": topic_id or (pending.get("topic_id") if pending else None),
        "message_id": message_id,
        "original_text": state.get("original_text", text),
        "actor_id": actor_id,
        "pending": pending,
        "db": services["db"],
        "gmail": services["gmail"],
        "telegram": services["telegram"],
        "llm": services["llm"],
    }


def _get_action_fn(action_name: str):
    """Map action name to execute function."""
    action_map = {
        "archive": archive.execute,
        "vip": vip.execute,
        "silence": silence.execute,
        "spam": spam.execute,
        "send_draft": reply.send_draft,
    }
    return action_map.get(action_name)


async def _mark_done(tg, chat_id, message_id, status, original_text):
    """Append status to original message and remove buttons."""
    timestamp = datetime.now().strftime("%d/%m às %H:%M")
    new_text = f"{original_text}\n\n───\n{status} em {timestamp}"
    await tg.edit_message(message_id, new_text, chat_id=str(chat_id),
                          reply_markup={"inline_keyboard": []})
