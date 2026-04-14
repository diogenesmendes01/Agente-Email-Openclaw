"""Step: validate Telegram bot token and discover chat_id."""

from pathlib import Path

import requests

from setup_steps.common import (
    step_header, ask, confirm, success, error, warning, spinner,
)

TELEGRAM_API = "https://api.telegram.org/bot{token}"


def validate_token(token: str) -> dict | None:
    """Call getMe to validate a bot token. Returns bot info or None."""
    try:
        resp = requests.get(f"{TELEGRAM_API.format(token=token)}/getMe", timeout=10)
        data = resp.json()
        if data.get("ok"):
            return data["result"]
    except Exception:
        pass
    return None


def _flush_old_updates(token: str):
    """Flush old updates so discover_chat_id only sees fresh ones."""
    try:
        resp = requests.get(
            f"{TELEGRAM_API.format(token=token)}/getUpdates",
            params={"limit": 1, "offset": -1}, timeout=10,
        )
        data = resp.json()
        if data.get("ok") and data["result"]:
            last_id = data["result"][-1]["update_id"]
            # Confirm the offset so Telegram drops all older updates
            requests.get(
                f"{TELEGRAM_API.format(token=token)}/getUpdates",
                params={"offset": last_id + 1, "limit": 1}, timeout=10,
            )
    except Exception:
        pass


def discover_chat_id(token: str) -> tuple:
    """Call getUpdates and return (chat_id, title) from the most recent group/supergroup message."""
    try:
        resp = requests.get(
            f"{TELEGRAM_API.format(token=token)}/getUpdates",
            params={"limit": 20, "timeout": 5}, timeout=15,
        )
        data = resp.json()
        if data.get("ok") and data["result"]:
            # Prefer group/supergroup chats over private DMs
            for update in reversed(data["result"]):
                msg = update.get("message") or update.get("channel_post")
                if msg and "chat" in msg:
                    chat = msg["chat"]
                    if chat.get("type") in ("group", "supergroup"):
                        return chat["id"], chat.get("title", "")
            # Fallback: return any chat found (with warning to user)
            for update in reversed(data["result"]):
                msg = update.get("message") or update.get("channel_post")
                if msg and "chat" in msg:
                    chat = msg["chat"]
                    return chat["id"], chat.get("title", chat.get("first_name", ""))
    except Exception:
        pass
    return None, None


def run(env: dict) -> bool:
    """Validate Telegram config."""
    step_header(4, "Telegram")

    token = env.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        error("TELEGRAM_BOT_TOKEN não configurado")
        return False

    with spinner("Validando token do bot..."):
        bot_info = validate_token(token)

    if bot_info:
        success(f"Bot conectado: @{bot_info.get('username', '?')}")
    else:
        error("Token inválido ou bot não acessível")
        return False

    # Chat ID discovery
    chat_id = env.get("TELEGRAM_CHAT_ID", "")
    if not chat_id:
        warning("Chat ID não configurado")
        if confirm("Deseja tentar descobrir automaticamente?"):
            with spinner("Limpando updates antigos..."):
                _flush_old_updates(token)
            print("    Envie /start no grupo do bot e pressione Enter...")
            input("    [Enter para continuar]")
            with spinner("Buscando chat_id..."):
                found_id, found_title = discover_chat_id(token)
            if found_id:
                success(f"Chat encontrado: {found_title} (ID: {found_id})")
                if confirm(f"Usar chat_id = {found_id}?"):
                    env["TELEGRAM_CHAT_ID"] = str(found_id)
            else:
                warning("Nenhuma mensagem recente encontrada. Configure o TELEGRAM_CHAT_ID manualmente no .env")
    else:
        success(f"Chat ID configurado: {chat_id}")

    return True
