"""Create task in PostgreSQL."""
import logging

logger = logging.getLogger(__name__)

PRIORITY_MAP = {
    "critical": "Crítica",
    "high": "Alta",
    "medium": "Média",
    "low": "Baixa",
}


async def execute(ctx: dict) -> str:
    """Create task from email. Returns status string."""
    try:
        subject = ctx.get("subject", "")
        urgency = ctx.get("urgency", "medium")
        details = ctx.get("task_details", "") or subject
        title = f"[{urgency.upper()}] {details[:90]}"
        priority = PRIORITY_MAP.get(urgency, "Média")

        task_id = await ctx["db"].create_task(
            account_id=ctx["account_id"],
            title=title,
            priority=priority,
            email_id=ctx["email_id"],
        )
        return f"📋 Tarefa criada: {title[:50]}"
    except Exception as e:
        logger.error(f"Task error: {e}")
        return "❌ Erro ao criar tarefa"
