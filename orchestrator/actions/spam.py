"""Mark as spam in Gmail + blacklist."""
import logging

logger = logging.getLogger(__name__)


async def execute(ctx: dict) -> str:
    """Mark as spam and blacklist sender. Returns status string."""
    try:
        success = await ctx["gmail"].mark_as_spam(ctx["email_id"], ctx["account"])
        if success:
            await ctx["db"].add_to_blacklist(ctx["account_id"], ctx["sender"], "marcado como spam")
            return "🗑️ Spam"
        return "❌ Erro ao marcar como spam"
    except Exception as e:
        logger.error(f"Spam error: {e}")
        return "❌ Erro ao marcar como spam"
