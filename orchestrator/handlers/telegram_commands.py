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


async def handle_command(message: dict, services: dict):
    """Route and handle /config_* commands."""
    chat_id = message.get("chat", {}).get("id")
    actor_id = message.get("from", {}).get("id")
    text = message.get("text", "").strip()
    parts = text.split(maxsplit=1)
    cmd = parts[0].split("@")[0]
    args = parts[1] if len(parts) > 1 else ""

    db = services["db"]
    tg = services["telegram"]

    if cmd == "/config_identidade":
        await _start_config_identidade(chat_id, actor_id, db, tg)
    elif cmd == "/config_playbook":
        await _start_config_playbook(chat_id, actor_id, db, tg)
    elif cmd == "/config_playbook_list":
        await _list_playbooks(chat_id, actor_id, db, tg)
    elif cmd == "/config_playbook_delete":
        await _delete_playbook(chat_id, args, db, tg)
    elif cmd == "/help_config":
        await tg.send_text(chat_id, (
            "<b>Comandos de Configuração:</b>\n\n"
            "/config_identidade — Nome, CNPJ, tom, assinatura\n"
            "/config_playbook — Criar novo playbook\n"
            "/config_playbook_list — Listar playbooks ativos\n"
            "/config_playbook_delete &lt;id&gt; — Remover playbook"
        ))


async def _start_config_identidade(chat_id, actor_id, db, tg):
    """Start identity configuration conversation."""
    account = await db.get_account_by_topic(chat_id)
    account_id = account["id"] if account else None
    await db.create_pending_action(
        account_id, "config", "config_identidade", actor_id, chat_id, None,
        {"step": "company_name"},
    )
    await tg.send_text(chat_id, (
        "\U0001f3e2 <b>Configuração de Identidade</b>\n\n"
        "Qual o nome da empresa?"
    ))


async def _start_config_playbook(chat_id, actor_id, db, tg):
    """Start playbook creation conversation."""
    account = await db.get_account_by_topic(chat_id)
    account_id = account["id"] if account else None
    await db.create_pending_action(
        account_id, "config", "config_playbook", actor_id, chat_id, None,
        {"step": "trigger"},
    )
    await tg.send_text(chat_id, (
        "\U0001f4cb <b>Novo Playbook</b>\n\n"
        "Descreva o gatilho (quando este playbook deve ativar?).\n"
        "Ex: 'dúvida sobre boleto ou proposta'"
    ))


async def _list_playbooks(chat_id, actor_id, db, tg):
    """List active playbooks."""
    account = await db.get_account_by_topic(chat_id)
    account_id = account["id"] if account else None
    if not account_id:
        await tg.send_text(chat_id, "\u274c Conta não encontrada para este tópico.")
        return
    profile = await db.get_company_profile(account_id)
    if not profile:
        await tg.send_text(chat_id, "\u274c Nenhum perfil de empresa configurado. Use /config_identidade primeiro.")
        return
    playbooks = await db.get_playbooks(profile["id"])
    if not playbooks:
        await tg.send_text(chat_id, "\U0001f4cb Nenhum playbook configurado. Use /config_playbook para criar.")
        return
    lines = ["<b>\U0001f4cb Playbooks Ativos:</b>\n"]
    for p in playbooks:
        auto = "\U0001f916 Auto" if p.get("auto_respond") else "\U0001f464 Manual"
        lines.append(f"• <b>#{p['id']}</b> — {p['trigger_description']} [{auto}]")
    await tg.send_text(chat_id, "\n".join(lines))


async def _delete_playbook(chat_id, args, db, tg):
    """Delete a playbook by ID."""
    try:
        playbook_id = int(args.strip())
        await db.delete_playbook(playbook_id)
        await tg.send_text(chat_id, f"\u2705 Playbook #{playbook_id} removido.")
    except (ValueError, TypeError):
        await tg.send_text(chat_id, "\u274c Uso: /config_playbook_delete <id>")


async def handle_config_response(message: dict, pending: dict, services: dict):
    """Handle follow-up text messages for multi-step config conversations."""
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "").strip()
    db = services["db"]
    tg = services["telegram"]

    state = json.loads(pending["state"]) if isinstance(pending["state"], str) else pending["state"]
    action_type = pending["action_type"]

    if action_type == "config_identidade":
        await _continue_config_identidade(chat_id, text, pending, state, db, tg)
    elif action_type == "config_playbook":
        await _continue_config_playbook(chat_id, text, pending, state, db, tg)


async def _continue_config_identidade(chat_id, text, pending, state, db, tg):
    """Continue multi-step identity configuration."""
    step = state.get("step")

    if step == "company_name":
        state["company_name"] = text
        state["step"] = "cnpj"
        await db.update_pending_state(pending["id"], state)
        await tg.send_text(chat_id, "CNPJ da empresa? (ou 'pular')")

    elif step == "cnpj":
        state["cnpj"] = text if text.lower() != "pular" else None
        state["step"] = "tone"
        await db.update_pending_state(pending["id"], state)
        await tg.send_text(chat_id, "Tom das respostas? (ex: 'formal, empático, objetivo')")

    elif step == "tone":
        state["tone"] = text
        state["step"] = "signature"
        await db.update_pending_state(pending["id"], state)
        await tg.send_text(chat_id, "Assinatura para emails? (ex: 'Atenciosamente, Equipe CodeWave')")

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
        ))


async def _continue_config_playbook(chat_id, text, pending, state, db, tg):
    """Continue multi-step playbook creation."""
    step = state.get("step")

    if step == "trigger":
        state["trigger"] = text
        state["step"] = "template"
        await db.update_pending_state(pending["id"], state)
        await tg.send_text(chat_id, (
            "Escreva o template de resposta.\n"
            "Pode usar {nome_contato} para o nome do remetente."
        ))

    elif step == "template":
        state["template"] = text
        state["step"] = "auto"
        await db.update_pending_state(pending["id"], state)
        await tg.send_text(chat_id, "Responder automaticamente? (sim/não)")

    elif step == "auto":
        auto = text.lower().startswith("s")
        profile = await db.get_company_profile(pending.get("account_id"))
        if not profile:
            await tg.send_text(chat_id, "\u274c Configure a identidade primeiro: /config_identidade")
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
        ))
