"""Add sender to VIP list."""
import logging

logger = logging.getLogger(__name__)


async def execute(ctx: dict) -> str:
    """Add sender as VIP. Returns status string."""
    try:
        sender = ctx["sender"]
        name = sender.split("@")[0] if "@" in sender else sender
        success = await ctx["db"].add_vip(ctx["account_id"], sender, name)
        if success:
            return f"⭐ VIP: {sender}"
        return f"⭐ {sender} já é VIP"
    except Exception as e:
        logger.error(f"VIP error: {e}")
        return "❌ Erro ao adicionar VIP"
