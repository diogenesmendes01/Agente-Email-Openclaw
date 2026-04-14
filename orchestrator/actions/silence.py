"""Add sender to blacklist (silence)."""
import logging

logger = logging.getLogger(__name__)


async def execute(ctx: dict) -> str:
    """Silence sender. Returns status string."""
    try:
        sender = ctx["sender"]
        success = await ctx["db"].add_to_blacklist(ctx["account_id"], sender, "silenciado pelo usuário")
        if success:
            return f"🔇 Silenciado: {sender}"
        return f"🔇 {sender} já está silenciado"
    except Exception as e:
        logger.error(f"Silence error: {e}")
        return "❌ Erro ao silenciar"
