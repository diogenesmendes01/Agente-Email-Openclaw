"""Custom reply — LLM generate + send via Gmail. Multi-step with pending state."""
import json
import logging

logger = logging.getLogger(__name__)


async def start_reply(ctx: dict) -> bool:
    """Step 1: Prompt user for instruction. Returns True on success."""
    try:
        state = {
            "original_text": ctx.get("original_text", ""),
            "account": ctx["account"],
            "sender": ctx.get("sender", ""),
        }
        await ctx["db"].create_pending_action(
            ctx.get("account_id"), ctx["email_id"], "custom_reply",
            ctx["actor_id"], ctx["chat_id"], ctx["message_id"], state,
            topic_id=ctx.get("topic_id"),
        )
        keyboard = {
            "inline_keyboard": [[
                {"text": "❌ Cancelar", "callback_data": f"cancel_custom_reply:{ctx['email_id']}"}
            ]]
        }
        await ctx["telegram"].send_text(
            ctx["chat_id"],
            "💬 <b>Digite sua instrução de resposta:</b>\n\n"
            "Exemplos:\n"
            "• diz que entrego na sexta\n"
            "• pede pra remarcar\n"
            "• aceita mas pede desconto",
            reply_markup=keyboard,
            thread_id=ctx.get("topic_id"),
        )
        return True
    except Exception as e:
        logger.error(f"Reply start error: {e}")
        return False


async def generate_reply(ctx: dict) -> str:
    """Step 2: Generate reply via LLM. Returns draft text or None."""
    try:
        pending = ctx["pending"]
        state = json.loads(pending["state"]) if isinstance(pending["state"], str) else pending["state"]
        original_text = state.get("original_text", "")
        instruction = ctx["instruction"]

        draft = await ctx["llm"].generate_custom_reply(original_text, instruction)
        if not draft:
            return None

        state["last_reply"] = draft
        state["waiting_instruction"] = False
        await ctx["db"].update_pending_state(pending["id"], state)

        keyboard = {
            "inline_keyboard": [[
                {"text": "✉️ Enviar", "callback_data": f"send_custom_draft:{ctx['email_id']}"},
                {"text": "✏️ Ajustar", "callback_data": f"adjust_custom_draft:{ctx['email_id']}"},
            ]]
        }
        await ctx["telegram"].send_text(
            ctx["chat_id"],
            f"💬 <b>RASCUNHO:</b>\n{draft[:500]}",
            reply_markup=keyboard,
            thread_id=ctx.get("topic_id"),
        )
        return draft
    except Exception as e:
        logger.error(f"Reply generate error: {e}")
        return None


async def send_draft(ctx: dict) -> str:
    """Step 3: Send the generated draft via Gmail. Returns status string."""
    try:
        pending = ctx["pending"]
        state = json.loads(pending["state"]) if isinstance(pending["state"], str) else pending["state"]
        draft_content = state.get("last_reply", "")
        sender = state.get("sender", "")

        if not draft_content:
            return "❌ Rascunho não encontrado"

        success = await ctx["gmail"].send_reply(ctx["email_id"], draft_content, ctx["account"], to=sender)
        if success:
            await ctx["db"].delete_pending_action(pending["id"])
            return "✉️ Respondido"
        return "❌ Erro ao enviar resposta"
    except Exception as e:
        logger.error(f"Reply send error: {e}")
        return "❌ Erro ao enviar resposta"
