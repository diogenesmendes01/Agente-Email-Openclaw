"""Telegram /config_* command handlers — conversational configuration."""
import json
import logging

logger = logging.getLogger(__name__)

# Commands this module handles
COMMANDS = {"/config_identidade", "/config_playbook", "/config_playbook_list", "/config_playbook_delete", "/help_config"}


def is_command(text: str) -> bool:
    """Check if text starts with a known command."""
    if not text:
        return False
    cmd = text.split()[0].split("@")[0]  # strip bot username
    return cmd in COMMANDS


def _resolve_topic_id(message: dict) -> int:
    """Extract the topic identifier for account resolution.

    In groups with topics, ``message_thread_id`` is the real topic.
    Falls back to ``chat.id`` for private chats / non-topic groups.
    """
    return message.get("message_thread_id") or message.get("chat", {}).get("id")


async def handle_command(message: dict, services: dict):
    """Route and handle /config_* commands."""
    chat_id = message.get("chat", {}).get("id")
    topic_id = _resolve_topic_id(message)
    actor_id = message.get("from", {}).get("id")
    text = message.get("text", "").strip()
    parts = text.split(maxsplit=1)
    cmd = parts[0].split("@")[0]
    args = parts[1] if len(parts) > 1 else ""

    db = services["db"]
    tg = services["telegram"]

    if cmd == "/config_identidade":
        await _start_config_identidade(chat_id, topic_id, actor_id, db, tg)
    elif cmd == "/config_playbook":
        await _start_config_playbook(chat_id, topic_id, actor_id, db, tg)
    elif cmd == "/config_playbook_list":
        await _list_playbooks(chat_id, topic_id, db, tg)
    elif cmd == "/config_playbook_delete":
        await _delete_playbook(chat_id, topic_id, args, db, tg)
    elif cmd == "/help_config":
        await tg.send_text(chat_id, (
            "<b>Comandos de Configuração:</b>\n\n"
            "/config_identidade — Nome, CNPJ, tom, assinatura\n"
            "/config_playbook — Criar novo playbook\n"
            "/config_playbook_list — Listar playbooks ativos\n"
            "/config_playbook_delete &lt;id&gt; — Remover playbook"
        ), thread_id=message.get("message_thread_id"))


async def _start_config_identidade(chat_id, topic_id, actor_id, db, tg):
    """Start identity configuration conversation."""
    thread_id = topic_id if topic_id != chat_id else None
    account = await db.get_account_by_topic(topic_id)
    if not account:
        await tg.send_text(chat_id, "❌ Conta não encontrada para este tópico. Vincule uma conta primeiro.", thread_id=thread_id)
        return
    account_id = account["id"]
    await db.create_pending_action(
        account_id, "config", "config_identidade", actor_id, chat_id, None,
        {"step": "company_name"}, topic_id=topic_id,
    )
    await tg.send_text(chat_id, (
        "\U0001f3e2 <b>Configuração de Identidade</b>\n\n"
        "Qual o nome da empresa?"
    ), thread_id=thread_id)


async def _start_config_playbook(chat_id, topic_id, actor_id, db, tg):
    """Start playbook creation conversation."""
    thread_id = topic_id if topic_id != chat_id else None
    account = await db.get_account_by_topic(topic_id)
    if not account:
        await tg.send_text(chat_id, "❌ Conta não encontrada para este tópico. Vincule uma conta primeiro.", thread_id=thread_id)
        return
    account_id = account["id"]
    await db.create_pending_action(
        account_id, "config", "config_playbook", actor_id, chat_id, None,
        {"step": "trigger"}, topic_id=topic_id,
    )
    await tg.send_text(chat_id, (
        "\U0001f4cb <b>Novo Playbook</b>\n\n"
        "Descreva o gatilho (quando este playbook deve ativar?).\n"
        "Ex: 'dúvida sobre boleto ou proposta'"
    ), thread_id=thread_id)


async def _list_playbooks(chat_id, topic_id, db, tg):
    """List active playbooks."""
    thread_id = topic_id if topic_id != chat_id else None
    account = await db.get_account_by_topic(topic_id)
    account_id = account["id"] if account else None
    if not account_id:
        await tg.send_text(chat_id, "\u274c Conta não encontrada para este tópico.", thread_id=thread_id)
        return
    profile = await db.get_company_profile(account_id)
    if not profile:
        await tg.send_text(chat_id, "\u274c Nenhum perfil de empresa configurado. Use /config_identidade primeiro.", thread_id=thread_id)
        return
    playbooks = await db.get_playbooks(profile["id"])
    if not playbooks:
        await tg.send_text(chat_id, "\U0001f4cb Nenhum playbook configurado. Use /config_playbook para criar.", thread_id=thread_id)
        return
    lines = ["<b>\U0001f4cb Playbooks Ativos:</b>\n"]
    for p in playbooks:
        auto = "\U0001f916 Auto" if p.get("auto_respond") else "\U0001f464 Manual"
        lines.append(f"• <b>#{p['id']}</b> — {p['trigger_description']} [{auto}]")
    await tg.send_text(chat_id, "\n".join(lines), thread_id=thread_id)


async def _delete_playbook(chat_id, topic_id, args, db, tg):
    """Delete a playbook by ID, validating ownership via current account."""
    thread_id = topic_id if topic_id != chat_id else None
    try:
        playbook_id = int(args.strip())
    except (ValueError, TypeError):
        await tg.send_text(chat_id, "\u274c Uso: /config_playbook_delete <id>", thread_id=thread_id)
        return

    # Resolve account → company to verify ownership
    account = await db.get_account_by_topic(topic_id)
    account_id = account["id"] if account else None
    if not account_id:
        await tg.send_text(chat_id, "\u274c Conta não encontrada para este tópico.", thread_id=thread_id)
        return
    profile = await db.get_company_profile(account_id)
    if not profile:
        await tg.send_text(chat_id, "\u274c Nenhum perfil de empresa configurado.", thread_id=thread_id)
        return

    deleted = await db.delete_playbook_owned(playbook_id, profile["id"])
    if deleted:
        await tg.send_text(chat_id, f"\u2705 Playbook #{playbook_id} removido.", thread_id=thread_id)
    else:
        await tg.send_text(chat_id, f"\u274c Playbook #{playbook_id} não encontrado ou não pertence a esta empresa.", thread_id=thread_id)


async def handle_config_response(message: dict, pending: dict, services: dict):
    """Handle follow-up text messages for multi-step config conversations."""
    chat_id = message.get("chat", {}).get("id")
    thread_id = message.get("message_thread_id")
    text = message.get("text", "").strip()
    db = services["db"]
    tg = services["telegram"]

    state = json.loads(pending["state"]) if isinstance(pending["state"], str) else pending["state"]
    action_type = pending["action_type"]

    if action_type == "config_identidade":
        await _continue_config_identidade(chat_id, text, pending, state, db, tg, thread_id=thread_id)
    elif action_type == "config_playbook":
        await _continue_config_playbook(chat_id, text, pending, state, db, tg, thread_id=thread_id)


async def _continue_config_identidade(chat_id, text, pending, state, db, tg, thread_id=None):
    """Continue multi-step identity configuration."""
    step = state.get("step")

    if step == "company_name":
        state["company_name"] = text
        state["step"] = "cnpj"
        await db.update_pending_state(pending["id"], state)
        await tg.send_text(chat_id, "CNPJ da empresa? (ou 'pular')", thread_id=thread_id)

    elif step == "cnpj":
        state["cnpj"] = text if text.lower() != "pular" else None
        state["step"] = "tone"
        await db.update_pending_state(pending["id"], state)
        await tg.send_text(chat_id, "Tom das respostas? (ex: 'formal, empático, objetivo')", thread_id=thread_id)

    elif step == "tone":
        state["tone"] = text
        state["step"] = "signature"
        await db.update_pending_state(pending["id"], state)
        await tg.send_text(chat_id, "Assinatura para emails? (ex: 'Atenciosamente, Equipe CodeWave')", thread_id=thread_id)

    elif step == "signature":
        state["signature"] = text
        await db.upsert_company_profile(
            account_id=pending.get("account_id"),
            company_name=state["company_name"],
            cnpj=state.get("cnpj"),
            tone=state.get("tone"),
            signature=state["signature"],
        )
        await db.delete_pending_action(pending["id"])
        await tg.send_text(chat_id, (
            f"\u2705 <b>Perfil salvo!</b>\n\n"
            f"\U0001f3e2 {state['company_name']}\n"
            f"\U0001f4dd Tom: {state.get('tone', '-')}\n"
            f"\u270d\ufe0f Assinatura: {state['signature'][:50]}"
        ), thread_id=thread_id)


async def _continue_config_playbook(chat_id, text, pending, state, db, tg, thread_id=None):
    """Continue multi-step playbook creation."""
    step = state.get("step")

    if step == "trigger":
        state["trigger"] = text
        state["step"] = "template"
        await db.update_pending_state(pending["id"], state)
        await tg.send_text(chat_id, (
            "Escreva o template de resposta.\n"
            "Pode usar {nome_contato} para o nome do remetente."
        ), thread_id=thread_id)

    elif step == "template":
        state["template"] = text
        state["step"] = "auto"
        await db.update_pending_state(pending["id"], state)
        await tg.send_text(chat_id, "Responder automaticamente? (sim/não)", thread_id=thread_id)

    elif step == "auto":
        auto = text.lower().startswith("s")
        profile = await db.get_company_profile(pending.get("account_id"))
        if not profile:
            await tg.send_text(chat_id, "\u274c Configure a identidade primeiro: /config_identidade", thread_id=thread_id)
            await db.delete_pending_action(pending["id"])
            return
        playbook_id = await db.create_playbook(
            company_id=profile["id"],
            trigger_description=state["trigger"],
            response_template=state["template"],
            auto_respond=auto,
        )
        await db.delete_pending_action(pending["id"])
        mode = "\U0001f916 Automático" if auto else "\U0001f464 Manual"
        await tg.send_text(chat_id, (
            f"\u2705 <b>Playbook #{playbook_id} criado!</b>\n\n"
            f"\U0001f3af Gatilho: {state['trigger']}\n"
            f"\U0001f4dd Modo: {mode}"
        ), thread_id=thread_id)
