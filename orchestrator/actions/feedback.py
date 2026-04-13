"""Reclassify email urgency — multi-step with pending state."""
import json
import logging

logger = logging.getLogger(__name__)

URGENCY_EMOJIS = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🟢",
}


def _urgency_keyboard(email_id: str) -> dict:
    """Build urgency selection keyboard."""
    return {
        "inline_keyboard": [
            [
                {"text": "🔴 Critical", "callback_data": f"set_urgency:critical:{email_id}"},
                {"text": "🟠 High", "callback_data": f"set_urgency:high:{email_id}"},
            ],
            [
                {"text": "🟡 Medium", "callback_data": f"set_urgency:medium:{email_id}"},
                {"text": "🟢 Low", "callback_data": f"set_urgency:low:{email_id}"},
            ],
            [
                {"text": "❌ Cancelar", "callback_data": f"cancel_reclassify:{email_id}"},
            ],
        ]
    }


def extract_urgency_from_text(text: str) -> str:
    """Extract urgency from message text."""
    text_lower = text.lower()
    for key in ("critical", "high", "medium", "low"):
        if key in text_lower:
            return key
    return "medium"


def extract_keywords_from_text(text: str) -> list:
    """Extract keywords from message text."""
    for line in text.split("\n"):
        if "Keywords:" in line or "Palavras-chave:" in line:
            kw_str = line.split(":")[-1].strip()
            return [k.strip() for k in kw_str.split(",") if k.strip()]
    return []


async def start_reclassify(ctx: dict) -> bool:
    """Step 1: Swap buttons to urgency selector. Returns True on success."""
    try:
        state = {
            "original_urgency": extract_urgency_from_text(ctx.get("original_text", "")),
            "keywords": extract_keywords_from_text(ctx.get("original_text", "")),
            "original_text": ctx.get("original_text", ""),
            "account": ctx["account"],
        }
        await ctx["db"].create_pending_action(
            ctx.get("account_id"), ctx["email_id"], "reclassify",
            ctx["actor_id"], ctx["chat_id"], ctx["message_id"], state,
        )
        keyboard = _urgency_keyboard(ctx["email_id"])
        await ctx["telegram"].edit_reply_markup(ctx["chat_id"], ctx["message_id"], keyboard)
        return True
    except Exception as e:
        logger.error(f"Reclassify start error: {e}")
        return False


async def complete_reclassify(ctx: dict) -> str:
    """Step 2: Save feedback, update message. Returns status string."""
    try:
        pending = ctx["pending"]
        state = json.loads(pending["state"]) if isinstance(pending["state"], str) else pending["state"]
        new_urgency = ctx["new_urgency"]
        original_urgency = state.get("original_urgency", "medium")
        keywords = state.get("keywords", [])

        await ctx["db"].save_feedback(
            ctx.get("account_id"), ctx["email_id"], ctx.get("sender", ""),
            original_urgency, new_urgency, keywords,
        )
        await ctx["db"].delete_pending_action(pending["id"])

        return f"🔄 {original_urgency.upper()} → {new_urgency.upper()}"
    except Exception as e:
        logger.error(f"Reclassify complete error: {e}")
        return "❌ Erro na reclassificação"
