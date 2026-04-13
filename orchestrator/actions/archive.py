"""Archive email in Gmail."""
import logging

logger = logging.getLogger(__name__)


async def execute(ctx: dict) -> str:
    """Archive email. Returns status string."""
    try:
        success = await ctx["gmail"].archive_email(ctx["email_id"], ctx["account"])
        if success:
            return "✅ Arquivado"
        return "❌ Erro ao arquivar"
    except Exception as e:
        logger.error(f"Archive error: {e}")
        return "❌ Erro ao arquivar"
