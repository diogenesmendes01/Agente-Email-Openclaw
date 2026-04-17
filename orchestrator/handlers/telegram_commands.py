"""Telegram /config_* command handlers — conversational configuration."""
import json
import logging

logger = logging.getLogger(__name__)

# Commands this module handles
COMMANDS = {
    "/config_identidade", "/config_playbook", "/config_playbook_list",
    "/config_playbook_delete", "/help_config", "/custos",
    "/config_modelo",
    # PR 2: PDF robust handling
    "/pdf_senha", "/pdf_senhas", "/pdf_senha_remove", "/config_documentos",
    # PR 3: 3-layer prompt architecture
    "/config_prompt", "/prompt_ver", "/prompt_reset", "/prompt_regras",
}


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
    elif cmd == "/custos":
        await _show_costs(chat_id, topic_id, args, db, tg, services)
    elif cmd == "/config_modelo":
        await _handle_config_modelo(chat_id, topic_id, actor_id, args, db, tg, services)
    elif cmd == "/pdf_senha":
        await _handle_pdf_senha(chat_id, topic_id, actor_id, args, db, tg)
    elif cmd == "/pdf_senhas":
        await _handle_pdf_senhas(chat_id, topic_id, db, tg)
    elif cmd == "/pdf_senha_remove":
        await _handle_pdf_senha_remove(chat_id, topic_id, actor_id, args, db, tg)
    elif cmd == "/config_documentos":
        await _start_config_documentos(chat_id, topic_id, actor_id, db, tg)
    elif cmd == "/config_prompt":
        await _start_config_prompt(chat_id, topic_id, actor_id, db, tg)
    elif cmd == "/prompt_ver":
        await _show_prompt_ver(chat_id, topic_id, db, tg)
    elif cmd == "/prompt_reset":
        await _start_prompt_reset(chat_id, topic_id, actor_id, db, tg)
    elif cmd == "/prompt_regras":
        await _show_prompt_regras(chat_id, topic_id, tg)
    elif cmd == "/help_config":
        await tg.send_text(chat_id, (
            "<b>Comandos de Configuração:</b>\n\n"
            "/config_identidade — Nome, CNPJ, tom, assinatura\n"
            "/config_playbook — Criar novo playbook\n"
            "/config_playbook_list — Listar playbooks ativos\n"
            "/config_playbook_delete &lt;id&gt; — Remover playbook\n"
            "/config_modelo — Ver/trocar modelo de IA\n"
            "/config_modelo listar 20 — Listar 20 modelos por preço\n"
            "/custos — Relatório de custos API (7 dias)\n"
            "/custos 30 — Relatório dos últimos 30 dias\n"
            "\n<b>PDFs protegidos:</b>\n"
            "/pdf_senha &lt;pattern&gt; &lt;senha&gt; — Cadastrar senha\n"
            "/pdf_senhas — Listar senhas cadastradas\n"
            "/pdf_senha_remove &lt;pattern&gt; — Remover senhas de um remetente\n"
            "/config_documentos — CPF/CNPJ/nasc. (inferência de senha, opt-in)\n"
            "\n<b>Prompts (3 camadas):</b>\n"
            "/prompt_ver — ver configuração atual e preview\n"
            "/prompt_regras — ver regras fixas (Camada 1)\n"
            "/config_prompt — editar customização da conta\n"
            "/prompt_reset — voltar ao default"
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


async def _handle_config_modelo(chat_id, topic_id, actor_id, args, db, tg, services):
    """Handle /config_modelo command — view, change, or browse models."""
    thread_id = topic_id if topic_id != chat_id else None

    account = await db.get_account_by_topic(topic_id)
    if not account:
        await tg.send_text(chat_id, "❌ Conta não encontrada para este tópico.", thread_id=thread_id)
        return

    account_id = account["id"]
    model_registry = services.get("model_registry")

    if not model_registry:
        await tg.send_text(chat_id, "❌ Serviço de modelos não disponível.", thread_id=thread_id)
        return

    args = args.strip().lower() if args else ""

    # /config_modelo listar [N] — browse all models by price
    if args.startswith("listar"):
        parts = args.split()
        limit = 20
        if len(parts) > 1:
            try:
                limit = min(max(int(parts[1]), 5), 100)
            except ValueError:
                pass

        models = await model_registry.list_models(limit=limit)
        if not models:
            await tg.send_text(chat_id, "❌ Não foi possível carregar modelos. Tente novamente.", thread_id=thread_id)
            return

        lines = [f"<b>📋 Top {len(models)} modelos por preço:</b>\n"]
        for i, m in enumerate(models, 1):
            tag = "🆓" if m.is_free else "💰"
            lines.append(f"{i}. {tag} <code>{m.id}</code>")
            lines.append(f"   {m.name} — {m.price_label()}")
        lines.append(f"\n💡 Para usar: /config_modelo usar &lt;id&gt;")
        # Split if too long
        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:4000] + "\n..."
        await tg.send_text(chat_id, text, thread_id=thread_id)
        return

    # /config_modelo buscar <query> — search models
    if args.startswith("buscar"):
        query = args.replace("buscar", "", 1).strip()
        if not query:
            await tg.send_text(chat_id, "💡 Uso: /config_modelo buscar gemini", thread_id=thread_id)
            return
        results = await model_registry.search_models(query, limit=15)
        if not results:
            await tg.send_text(chat_id, f"❌ Nenhum modelo encontrado para '{query}'.", thread_id=thread_id)
            return
        lines = [f"<b>🔍 Resultados para '{query}':</b>\n"]
        for m in results:
            tag = "🆓" if m.is_free else "💰"
            lines.append(f"{tag} <code>{m.id}</code> — {m.price_label()}")
        lines.append(f"\n💡 Para usar: /config_modelo usar &lt;id&gt;")
        await tg.send_text(chat_id, "\n".join(lines), thread_id=thread_id)
        return

    # /config_modelo usar <model_id> — set model
    if args.startswith("usar"):
        model_id = args.replace("usar", "", 1).strip()
        if not model_id:
            await tg.send_text(chat_id, "💡 Uso: /config_modelo usar google/gemini-2.5-flash-preview", thread_id=thread_id)
            return
        # Validate model exists
        model_info = await model_registry.get_model(model_id)
        if not model_info:
            await tg.send_text(chat_id, f"❌ Modelo <code>{model_id}</code> não encontrado no OpenRouter.\n\n💡 Use /config_modelo buscar &lt;nome&gt; para procurar.", thread_id=thread_id)
            return
        # Save to DB
        await db.set_account_model(account_id, model_id)
        await tg.send_text(chat_id, (
            f"✅ <b>Modelo atualizado!</b>\n\n"
            f"🤖 {model_info.name}\n"
            f"📎 <code>{model_info.id}</code>\n"
            f"💰 {model_info.price_label()}\n"
            f"📏 Contexto: {model_info.context_length:,} tokens\n\n"
            f"O próximo email já será processado com este modelo."
        ), thread_id=thread_id)
        return

    # /config_modelo reset — volta pro padrão
    if args == "reset":
        await db.set_account_model(account_id, None)
        import os
        default = os.getenv("LLM_MODEL", "z-ai/glm-5-turbo")
        await tg.send_text(chat_id, f"✅ Modelo resetado para o padrão: <code>{default}</code>", thread_id=thread_id)
        return

    # /config_modelo (sem args) — show current + curated list
    model_config = await db.get_account_model(account_id)
    current_model = model_config["model"]

    import os
    default_model = os.getenv("LLM_MODEL", "z-ai/glm-5-turbo")
    display_model = current_model or default_model
    is_custom = bool(current_model)

    # Get current model info
    current_info = await model_registry.get_model(display_model)

    lines = ["<b>🤖 Configuração de Modelo</b>\n"]
    if current_info:
        tag = " (personalizado)" if is_custom else " (padrão)"
        lines.append(f"Atual: <b>{current_info.name}</b>{tag}")
        lines.append(f"ID: <code>{current_info.id}</code>")
        lines.append(f"Preço: {current_info.price_label()}")
    else:
        lines.append(f"Atual: <code>{display_model}</code>")

    # Show curated models
    curated = await model_registry.get_curated_models()
    if curated:
        lines.append("\n<b>⭐ Top 15 — Melhores para Email:</b>\n")

        free = [m for m in curated if m.is_free]
        budget = [m for m in curated if not m.is_free and m.avg_price < 1.0]
        mid = [m for m in curated if 1.0 <= m.avg_price < 5.0]
        premium = [m for m in curated if m.avg_price >= 5.0]

        if free:
            lines.append("🆓 <b>Gratuitos:</b>")
            for m in free:
                marker = " ✅" if m.id == display_model else ""
                lines.append(f"  • {m.name}{marker}\n    <code>{m.id}</code>")

        if budget:
            lines.append("\n💚 <b>Econômicos:</b>")
            for m in budget:
                marker = " ✅" if m.id == display_model else ""
                lines.append(f"  • {m.name} — {m.price_label()}{marker}\n    <code>{m.id}</code>")

        if mid:
            lines.append("\n💛 <b>Intermediários:</b>")
            for m in mid:
                marker = " ✅" if m.id == display_model else ""
                lines.append(f"  • {m.name} — {m.price_label()}{marker}\n    <code>{m.id}</code>")

        if premium:
            lines.append("\n💎 <b>Premium:</b>")
            for m in premium:
                marker = " ✅" if m.id == display_model else ""
                lines.append(f"  • {m.name} — {m.price_label()}{marker}\n    <code>{m.id}</code>")

    lines.append("\n<b>Comandos:</b>")
    lines.append("/config_modelo usar &lt;id&gt; — Trocar modelo")
    lines.append("/config_modelo listar 20 — Ver 20 modelos por preço")
    lines.append("/config_modelo buscar gemini — Buscar modelos")
    lines.append("/config_modelo reset — Voltar ao padrão")

    await tg.send_text(chat_id, "\n".join(lines), thread_id=thread_id)


async def _show_costs(chat_id, topic_id, args, db, tg, services):
    """Show API cost summary via Telegram."""
    thread_id = topic_id if topic_id != chat_id else None
    # Parse days from args (default 7)
    try:
        days = int(args.strip()) if args.strip() else 7
        days = min(max(days, 1), 90)
    except ValueError:
        days = 7

    account = await db.get_account_by_topic(topic_id)
    if not account:
        await tg.send_text(chat_id, "❌ Conta não encontrada para este tópico.", thread_id=thread_id)
        return

    metrics = services.get("metrics")
    if not metrics:
        await tg.send_text(chat_id, "❌ Serviço de métricas não disponível.", thread_id=thread_id)
        return

    summary = await metrics.get_cost_summary(account["id"], days)

    lines = [f"<b>💰 Relatório de Custos — Últimos {days} dias</b>\n"]
    lines.append(f"📧 Emails processados: <b>{summary['total_emails']}</b>")
    lines.append(f"⚙️ Tokens totais: <b>{summary['total_tokens']:,}</b>")
    lines.append(f"💵 Custo total: <b>${summary['total_cost_usd']:.4f}</b>")

    if summary["total_emails"] > 0:
        avg_cost = summary["total_cost_usd"] / summary["total_emails"]
        avg_tokens = summary["total_tokens"] // summary["total_emails"]
        lines.append(f"\n📊 <b>Média por email:</b>")
        lines.append(f"   Tokens: {avg_tokens:,}")
        lines.append(f"   Custo: ${avg_cost:.4f}")

    if summary["daily"]:
        lines.append(f"\n📅 <b>Por dia:</b>")
        for d in summary["daily"][:14]:  # max 14 days in message
            lines.append(f"  {d['date']} │ {d['emails']} emails │ {d['tokens']:,} tk │ ${d['cost_usd']:.4f}")

    await tg.send_text(chat_id, "\n".join(lines), thread_id=thread_id)


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
    elif action_type == "config_documentos":
        await _continue_config_documentos(chat_id, text, pending, state, db, tg, thread_id=thread_id)
    elif action_type == "config_prompt":
        await _continue_config_prompt(chat_id, text, pending, state, db, tg, thread_id=thread_id)
    elif action_type == "prompt_reset":
        await _continue_prompt_reset(chat_id, text, pending, state, db, tg, thread_id=thread_id)


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


# ──────────────────────────────────────────────────────────────────────────
# PR 2: PDF password management
# ──────────────────────────────────────────────────────────────────────────

_MASKED_PWD = "••••••"


def _format_datetime(dt) -> str:
    if not dt:
        return "nunca"
    try:
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(dt)


async def _handle_pdf_senha(chat_id, topic_id, actor_id, args, db, tg):
    """/pdf_senha <pattern> <senha> — cadastra senha de PDF encriptada."""
    thread_id = topic_id if topic_id != chat_id else None
    account = await db.get_account_by_topic(topic_id)
    if not account:
        await tg.send_text(chat_id, "\u274c Conta não encontrada para este tópico.", thread_id=thread_id)
        return

    parts = args.strip().split(maxsplit=1) if args else []
    if len(parts) < 2:
        await tg.send_text(chat_id, (
            "\U0001f511 <b>Cadastrar senha de PDF</b>\n\n"
            "Uso: <code>/pdf_senha &lt;pattern&gt; &lt;senha&gt;</code>\n\n"
            "Exemplos de pattern:\n"
            "• <code>*@bradesco.com.br</code> — qualquer remetente do domínio\n"
            "• <code>cobranca@empresa.com</code> — remetente literal\n\n"
            "A senha é armazenada criptografada (Fernet)."
        ), thread_id=thread_id)
        return

    pattern, senha = parts[0].strip(), parts[1].strip()
    if not pattern or not senha:
        await tg.send_text(chat_id, "\u274c Pattern e senha não podem ser vazios.", thread_id=thread_id)
        return

    try:
        from orchestrator.utils.crypto import encrypt, is_configured
    except Exception as e:
        await tg.send_text(chat_id, f"\u274c Erro ao carregar módulo de criptografia: {e}", thread_id=thread_id)
        return

    if not is_configured():
        await tg.send_text(chat_id, (
            "\u274c <b>PDF_PASSWORD_KEY não configurada</b>\n\n"
            "Gere uma chave Fernet e adicione ao <code>.env</code>:\n"
            "<code>python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"</code>"
        ), thread_id=thread_id)
        return

    try:
        enc = encrypt(senha)
    except Exception as e:
        await tg.send_text(chat_id, f"\u274c Erro ao encriptar senha: {e}", thread_id=thread_id)
        return

    row_id = await db.add_pdf_password(account["id"], pattern, enc, label=None)
    if row_id:
        await tg.send_text(chat_id, (
            f"\u2705 Senha cadastrada para <code>{pattern}</code>.\n"
            f"A senha será tentada automaticamente nos próximos PDFs deste remetente."
        ), thread_id=thread_id)
    else:
        await tg.send_text(chat_id, (
            f"\u2139\ufe0f Senha para <code>{pattern}</code> já estava cadastrada."
        ), thread_id=thread_id)


async def _handle_pdf_senhas(chat_id, topic_id, db, tg):
    """/pdf_senhas — lista senhas cadastradas (senha sempre mascarada)."""
    thread_id = topic_id if topic_id != chat_id else None
    account = await db.get_account_by_topic(topic_id)
    if not account:
        await tg.send_text(chat_id, "\u274c Conta não encontrada para este tópico.", thread_id=thread_id)
        return
    rows = await db.list_pdf_passwords(account["id"])
    if not rows:
        await tg.send_text(chat_id, (
            "\U0001f511 Nenhuma senha de PDF cadastrada.\n"
            "Use <code>/pdf_senha &lt;pattern&gt; &lt;senha&gt;</code>."
        ), thread_id=thread_id)
        return

    lines = ["<b>\U0001f511 Senhas de PDF cadastradas:</b>\n"]
    for r in rows:
        label = f" — {r['label']}" if r.get("label") else ""
        locked = ""
        if r.get("locked_until"):
            from datetime import datetime, timezone
            if r["locked_until"] > datetime.now(timezone.utc):
                locked = " \U0001f512 bloqueado"
        lines.append(
            f"• <code>{r['sender_pattern']}</code>{label} — senha: {_MASKED_PWD}\n"
            f"   último uso: {_format_datetime(r.get('last_used_at'))} "
            f"| usos: {r.get('use_count', 0)}{locked}"
        )
    await tg.send_text(chat_id, "\n".join(lines), thread_id=thread_id)


async def _handle_pdf_senha_remove(chat_id, topic_id, actor_id, args, db, tg):
    """/pdf_senha_remove <pattern> — remove todas as senhas de um pattern."""
    thread_id = topic_id if topic_id != chat_id else None
    pattern = (args or "").strip()
    if not pattern:
        await tg.send_text(chat_id, "Uso: <code>/pdf_senha_remove &lt;pattern&gt;</code>", thread_id=thread_id)
        return
    account = await db.get_account_by_topic(topic_id)
    if not account:
        await tg.send_text(chat_id, "\u274c Conta não encontrada para este tópico.", thread_id=thread_id)
        return
    removed = await db.remove_pdf_passwords(account["id"], pattern)
    if removed:
        await tg.send_text(chat_id, (
            f"\u2705 {removed} senha(s) removida(s) para <code>{pattern}</code>."
        ), thread_id=thread_id)
    else:
        await tg.send_text(chat_id, (
            f"\u2139\ufe0f Nenhuma senha cadastrada para <code>{pattern}</code>."
        ), thread_id=thread_id)


async def _start_config_documentos(chat_id, topic_id, actor_id, db, tg):
    """Conversational multi-step: CPF / CNPJ / data de nascimento."""
    thread_id = topic_id if topic_id != chat_id else None
    account = await db.get_account_by_topic(topic_id)
    if not account:
        await tg.send_text(chat_id, "\u274c Conta não encontrada para este tópico.", thread_id=thread_id)
        return
    try:
        from orchestrator.utils.crypto import is_configured
    except Exception as e:
        await tg.send_text(chat_id, f"\u274c Módulo de criptografia indisponível: {e}", thread_id=thread_id)
        return
    if not is_configured():
        await tg.send_text(chat_id, (
            "\u274c <b>PDF_PASSWORD_KEY não configurada</b> — impossível armazenar documentos "
            "com segurança. Configure a variável no .env e tente novamente."
        ), thread_id=thread_id)
        return

    await db.create_pending_action(
        account["id"], "config", "config_documentos", actor_id, chat_id, None,
        {"step": "cpf"}, topic_id=topic_id,
    )
    await tg.send_text(chat_id, (
        "\U0001f510 <b>Documentos pessoais</b>\n\n"
        "Estes dados serão armazenados <b>criptografados</b> (Fernet) e usados "
        "<u>exclusivamente</u> para tentar abrir PDFs protegidos por senha "
        "quando o corpo do email indicar que a senha é o CPF/CNPJ/nascimento.\n\n"
        "Envie o <b>CPF</b> (só números ou com pontos), ou <code>-</code> para pular."
    ), thread_id=thread_id)


async def _continue_config_documentos(chat_id, text, pending, state, db, tg, thread_id=None):
    """Continue multi-step documentos flow. Accepts '-' to skip any step."""
    import re as _re
    from orchestrator.utils.crypto import encrypt

    step = state.get("step")
    val = (text or "").strip()

    if step == "cpf":
        if val != "-":
            digits = _re.sub(r"\D", "", val)
            if len(digits) != 11:
                await tg.send_text(chat_id, (
                    "\u274c CPF inválido (precisa ter 11 dígitos). Tente de novo ou envie <code>-</code> para pular."
                ), thread_id=thread_id)
                return
            state["cpf"] = digits
        state["step"] = "cnpj"
        await db.update_pending_state(pending["id"], state)
        await tg.send_text(chat_id, "Envie o <b>CNPJ</b> (14 dígitos) ou <code>-</code> para pular.", thread_id=thread_id)

    elif step == "cnpj":
        if val != "-":
            digits = _re.sub(r"\D", "", val)
            if len(digits) != 14:
                await tg.send_text(chat_id, (
                    "\u274c CNPJ inválido (precisa ter 14 dígitos). Tente de novo ou envie <code>-</code> para pular."
                ), thread_id=thread_id)
                return
            state["cnpj"] = digits
        state["step"] = "birthdate"
        await db.update_pending_state(pending["id"], state)
        await tg.send_text(chat_id, (
            "Envie a <b>data de nascimento</b> no formato <code>DD/MM/AAAA</code> "
            "ou <code>-</code> para pular."
        ), thread_id=thread_id)

    elif step == "birthdate":
        if val != "-":
            m = _re.match(r"^(\d{2})/(\d{2})/(\d{4})$", val)
            if not m:
                await tg.send_text(chat_id, (
                    "\u274c Formato inválido. Use <code>DD/MM/AAAA</code> ou <code>-</code> para pular."
                ), thread_id=thread_id)
                return
            dd, mm, yyyy = m.group(1), m.group(2), m.group(3)
            state["birthdate"] = f"{yyyy}-{mm}-{dd}"
        state["step"] = "confirm"
        await db.update_pending_state(pending["id"], state)
        resumo = []
        resumo.append(f"CPF: {'***.***.***-' + state['cpf'][-2:] if state.get('cpf') else '—'}")
        resumo.append(f"CNPJ: {'**.***.***/****-' + state['cnpj'][-2:] if state.get('cnpj') else '—'}")
        resumo.append(f"Nascimento: {state.get('birthdate', '—')}")
        await tg.send_text(chat_id, (
            "Confirme os dados (serão criptografados antes de salvar):\n\n"
            + "\n".join(resumo)
            + "\n\nDigite <code>sim</code> para salvar ou <code>cancelar</code>."
        ), thread_id=thread_id)

    elif step == "confirm":
        if val.lower() not in ("sim", "s", "yes", "y"):
            await db.delete_pending_action(pending["id"])
            await tg.send_text(chat_id, "\u274c Cancelado. Nenhum dado foi salvo.", thread_id=thread_id)
            return
        cpf_enc = encrypt(state["cpf"]) if state.get("cpf") else None
        cnpj_enc = encrypt(state["cnpj"]) if state.get("cnpj") else None
        bd_enc = encrypt(state["birthdate"]) if state.get("birthdate") else None
        await db.upsert_account_documents(
            account_id=pending.get("account_id"),
            cpf_encrypted=cpf_enc,
            cnpj_encrypted=cnpj_enc,
            birthdate_encrypted=bd_enc,
        )
        await db.delete_pending_action(pending["id"])
        await tg.send_text(chat_id, (
            "\u2705 <b>Documentos salvos criptografados.</b>\n\n"
            "Serão usados apenas para inferir senhas de PDFs quando o corpo do email "
            "mencionar CPF/CNPJ/nascimento."
        ), thread_id=thread_id)


# ──────────────────────────────────────────────────────────────────────────
# PR 3: 3-layer prompt architecture — per-account prompt customization
# ──────────────────────────────────────────────────────────────────────────

PROMPT_MENU_TEXT = (
    "\U0001f4dd <b>Configurar prompt desta conta</b>\n\n"
    "O que deseja configurar?\n"
    "<b>1</b>) Tom adicional (ex: \"informal com emojis\")\n"
    "<b>2</b>) Instrucoes extras (uma por linha)\n"
    "<b>3</b>) Categorias extras (separadas por virgula)\n"
    "<b>4</b>) Tamanho do rascunho (curto|medio|longo)\n"
    "<b>5</b>) Instrucoes livres (max 500 chars, filtrado)\n"
    "<b>6</b>) Cancelar\n\n"
    "Envie o numero da opcao."
)


async def _start_config_prompt(chat_id, topic_id, actor_id, db, tg):
    """Start /config_prompt multi-step wizard."""
    thread_id = topic_id if topic_id != chat_id else None
    account = await db.get_account_by_topic(topic_id)
    if not account:
        await tg.send_text(chat_id, "\u274c Conta nao encontrada para este topico.", thread_id=thread_id)
        return
    await db.create_pending_action(
        account["id"], "config", "config_prompt", actor_id, chat_id, None,
        {"step": "menu"}, topic_id=topic_id,
    )
    await tg.send_text(chat_id, PROMPT_MENU_TEXT, thread_id=thread_id)


async def _continue_config_prompt(chat_id, text, pending, state, db, tg, thread_id=None):
    """Multi-step wizard for per-account prompt config."""
    from orchestrator.services.prompt_builder import (
        sanitize_user_freeform, MAX_FREEFORM_CHARS,
    )

    step = state.get("step")
    val = (text or "").strip()
    account_id = pending.get("account_id")

    if step == "menu":
        current = await db.get_account_prompt_config(account_id) or {}
        if val == "1":
            state["step"] = "set_tom"
            await db.update_pending_state(pending["id"], state)
            cur = current.get("tom_adicional") or "(nao definido)"
            await tg.send_text(chat_id, (
                f"<b>Tom adicional</b>\nAtual: <i>{cur}</i>\n\n"
                "Envie o novo tom ou <code>-</code> para limpar."
            ), thread_id=thread_id)
        elif val == "2":
            state["step"] = "set_extras"
            await db.update_pending_state(pending["id"], state)
            extras = current.get("instrucoes_extras") or []
            cur = "\n".join(f"- {e}" for e in extras) if extras else "(nenhuma)"
            await tg.send_text(chat_id, (
                f"<b>Instrucoes extras</b>\nAtuais:\n{cur}\n\n"
                "Envie uma instrucao por linha (ou <code>-</code> para limpar)."
            ), thread_id=thread_id)
        elif val == "3":
            state["step"] = "set_cats"
            await db.update_pending_state(pending["id"], state)
            cats = current.get("categorias_extras") or []
            cur = ", ".join(cats) if cats else "(nenhuma)"
            await tg.send_text(chat_id, (
                f"<b>Categorias extras</b>\nAtuais: {cur}\n\n"
                "Envie as categorias separadas por virgula (ou <code>-</code> para limpar)."
            ), thread_id=thread_id)
        elif val == "4":
            state["step"] = "set_tamanho"
            await db.update_pending_state(pending["id"], state)
            cur = current.get("tamanho_rascunho") or "(padrao: medio)"
            await tg.send_text(chat_id, (
                f"<b>Tamanho do rascunho</b>\nAtual: {cur}\n\n"
                "Envie <code>curto</code>, <code>medio</code> ou <code>longo</code> "
                "(ou <code>-</code> para limpar)."
            ), thread_id=thread_id)
        elif val == "5":
            state["step"] = "set_livres"
            await db.update_pending_state(pending["id"], state)
            cur = current.get("instrucoes_livres") or "(nada)"
            await tg.send_text(chat_id, (
                f"<b>Instrucoes livres</b> (max {MAX_FREEFORM_CHARS} chars)\n"
                f"Atual: <i>{cur[:200]}</i>\n\n"
                "Envie o texto, ou <code>-</code> para limpar.\n"
                "Palavras como <i>ignore / override / desconsidere</i> serao rejeitadas."
            ), thread_id=thread_id)
        elif val == "6":
            await db.delete_pending_action(pending["id"])
            await tg.send_text(chat_id, "Cancelado.", thread_id=thread_id)
        else:
            await tg.send_text(chat_id, (
                "\u274c Opcao invalida. Envie um numero de 1 a 6.\n\n" + PROMPT_MENU_TEXT
            ), thread_id=thread_id)
        return

    if step == "set_tom":
        new_val = None if val == "-" else val
        await db.update_account_prompt_config_field(account_id, "tom_adicional", new_val)
        await db.delete_pending_action(pending["id"])
        await tg.send_text(chat_id, "\u2705 Tom atualizado.", thread_id=thread_id)

    elif step == "set_extras":
        if val == "-":
            lst = []
        else:
            lst = [ln.strip().lstrip("-").strip() for ln in val.splitlines() if ln.strip()]
        await db.update_account_prompt_config_field(account_id, "instrucoes_extras", lst)
        await db.delete_pending_action(pending["id"])
        await tg.send_text(chat_id, f"\u2705 Instrucoes atualizadas ({len(lst)} itens).", thread_id=thread_id)

    elif step == "set_cats":
        if val == "-":
            cats = []
        else:
            cats = [c.strip() for c in val.split(",") if c.strip()]
        await db.update_account_prompt_config_field(account_id, "categorias_extras", cats)
        await db.delete_pending_action(pending["id"])
        await tg.send_text(chat_id, f"\u2705 Categorias extras atualizadas ({len(cats)} itens).", thread_id=thread_id)

    elif step == "set_tamanho":
        if val == "-":
            new_val = None
        elif val.lower() in ("curto", "medio", "médio", "longo"):
            new_val = val.lower().replace("é", "e")
        else:
            await tg.send_text(chat_id, (
                "\u274c Valor invalido. Use <code>curto</code>, <code>medio</code> ou <code>longo</code>."
            ), thread_id=thread_id)
            return
        await db.update_account_prompt_config_field(account_id, "tamanho_rascunho", new_val)
        await db.delete_pending_action(pending["id"])
        await tg.send_text(chat_id, "\u2705 Tamanho do rascunho atualizado.", thread_id=thread_id)

    elif step == "set_livres":
        if val == "-":
            await db.update_account_prompt_config_field(account_id, "instrucoes_livres", None)
            await db.delete_pending_action(pending["id"])
            await tg.send_text(chat_id, "\u2705 Instrucoes livres removidas.", thread_id=thread_id)
            return
        clean, warnings = sanitize_user_freeform(val)
        if warnings:
            await db.delete_pending_action(pending["id"])
            await tg.send_text(chat_id, (
                "\u274c <b>Rejeitado</b>: texto contem palavras que podem burlar as "
                "regras de sistema (ignore, override, desconsidere, etc.). Use uma "
                "formulacao positiva: em vez de \"ignore X\" prefira \"evite mencionar X\"."
            ), thread_id=thread_id)
            return
        await db.update_account_prompt_config_field(account_id, "instrucoes_livres", clean)
        await db.delete_pending_action(pending["id"])
        await tg.send_text(chat_id, (
            f"\u2705 Instrucoes livres salvas ({len(clean)} chars)."
        ), thread_id=thread_id)


async def _show_prompt_ver(chat_id, topic_id, db, tg):
    """/prompt_ver — shows config layers and a preview (no LLM call)."""
    from orchestrator.services.prompt_builder import (
        PromptBuilder, SYSTEM_RULES,
    )

    thread_id = topic_id if topic_id != chat_id else None
    account = await db.get_account_by_topic(topic_id)
    if not account:
        await tg.send_text(chat_id, "\u274c Conta nao encontrada para este topico.", thread_id=thread_id)
        return
    cfg = await db.get_account_prompt_config(account["id"]) or {}

    lines = [
        "\U0001f4cb <b>Configuracao de prompts desta conta</b>\n",
        "<b>[CAMADA 1 — Sistema (fixo)]</b>",
        f"\u2705 {len(SYSTEM_RULES)} regras inviolaveis aplicadas (use /prompt_regras para ver).\n",
        "<b>[CAMADA 2 — Tarefa (padrao)]</b>",
        "\u2022 Summary: max 2 frases, denso factual.",
        "\u2022 Classification: 7 categorias + 4 urgencias.",
        "\u2022 Action: tamanho medio, cita valores/datas.\n",
    ]
    if cfg:
        lines.append("<b>[CAMADA 3 — Sua configuracao]</b>")
        if cfg.get("tom_adicional"):
            lines.append(f"\u2022 Tom adicional: {cfg['tom_adicional']}")
        ie = cfg.get("instrucoes_extras") or []
        if ie:
            lines.append(f"\u2022 Instrucoes extras: ({len(ie)} itens)")
            for it in ie[:5]:
                lines.append(f"   - {it}")
        ce = cfg.get("categorias_extras") or []
        if ce:
            lines.append(f"\u2022 Categorias extras: {', '.join(ce)}")
        if cfg.get("tamanho_rascunho"):
            lines.append(f"\u2022 Tamanho do rascunho: {cfg['tamanho_rascunho']}")
        if cfg.get("instrucoes_livres"):
            lines.append(f"\u2022 Instrucoes livres: <i>{cfg['instrucoes_livres'][:160]}</i>")
    else:
        lines.append("<b>[CAMADA 3 — Sua configuracao]</b>")
        lines.append("(nenhuma — usando defaults)")

    pb = PromptBuilder()
    preview = pb.build_preview("action", custom=cfg if cfg else None)
    preview_short = preview if len(preview) <= 1500 else preview[:1500] + "\n..."
    lines.append("\n<b>Preview (acao):</b>")
    lines.append(f"<pre>{_html_escape(preview_short)}</pre>")
    lines.append("\n<b>Comandos:</b>")
    lines.append("/config_prompt — editar")
    lines.append("/prompt_reset — voltar ao default")
    lines.append("/prompt_regras — ver regras fixas")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n..."
    await tg.send_text(chat_id, text, thread_id=thread_id)


async def _start_prompt_reset(chat_id, topic_id, actor_id, db, tg):
    """/prompt_reset — confirmation flow."""
    thread_id = topic_id if topic_id != chat_id else None
    account = await db.get_account_by_topic(topic_id)
    if not account:
        await tg.send_text(chat_id, "\u274c Conta nao encontrada para este topico.", thread_id=thread_id)
        return
    await db.create_pending_action(
        account["id"], "config", "prompt_reset", actor_id, chat_id, None,
        {"step": "confirm"}, topic_id=topic_id,
    )
    await tg.send_text(chat_id, (
        "\u26a0\ufe0f Isto vai apagar TODA a configuracao customizada de prompt "
        "desta conta. Confirmar? Envie <code>sim</code> ou <code>nao</code>."
    ), thread_id=thread_id)


async def _continue_prompt_reset(chat_id, text, pending, state, db, tg, thread_id=None):
    val = (text or "").strip().lower()
    if val in ("sim", "s", "yes", "y"):
        await db.delete_account_prompt_config(pending.get("account_id"))
        await db.delete_pending_action(pending["id"])
        await tg.send_text(chat_id, "\u2705 Configuracao customizada apagada. Usando defaults.", thread_id=thread_id)
    else:
        await db.delete_pending_action(pending["id"])
        await tg.send_text(chat_id, "Cancelado. Nada foi alterado.", thread_id=thread_id)


async def _show_prompt_regras(chat_id, topic_id, tg):
    """/prompt_regras — shows Layer 1 inviolable rules."""
    from orchestrator.services.prompt_builder import layer1_text
    thread_id = topic_id if topic_id != chat_id else None
    text = (
        "\U0001f512 <b>REGRAS DE SISTEMA (Camada 1 — fixas)</b>\n\n"
        f"<pre>{_html_escape(layer1_text())}</pre>\n\n"
        "Estas regras sao aplicadas antes de qualquer configuracao de tarefa ou de conta, "
        "e NAO podem ser sobrescritas por /config_prompt."
    )
    await tg.send_text(chat_id, text, thread_id=thread_id)


def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
    )
