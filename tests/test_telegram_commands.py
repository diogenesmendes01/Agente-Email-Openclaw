"""Tests for Telegram /config commands."""
import pytest
from unittest.mock import AsyncMock


def _make_services():
    return {"db": AsyncMock(), "telegram": AsyncMock(), "llm": AsyncMock(), "gmail": AsyncMock()}


@pytest.mark.asyncio
async def test_config_identidade_starts_conversation():
    from orchestrator.handlers.telegram_commands import handle_command
    services = _make_services()
    msg = {"chat": {"id": 100}, "from": {"id": 42}, "text": "/config_identidade"}
    services["db"].get_account_by_topic.return_value = {"id": 1}
    await handle_command(msg, services)
    services["telegram"].send_text.assert_called_once()
    call_text = services["telegram"].send_text.call_args[0][1]
    assert "nome da empresa" in call_text.lower()


@pytest.mark.asyncio
async def test_config_playbook_starts_conversation():
    from orchestrator.handlers.telegram_commands import handle_command
    services = _make_services()
    msg = {"chat": {"id": 100}, "from": {"id": 42}, "text": "/config_playbook"}
    services["db"].get_account_by_topic.return_value = {"id": 1}
    await handle_command(msg, services)
    services["telegram"].send_text.assert_called_once()


@pytest.mark.asyncio
async def test_config_playbook_list():
    from orchestrator.handlers.telegram_commands import handle_command
    services = _make_services()
    msg = {"chat": {"id": 100}, "from": {"id": 42}, "text": "/config_playbook_list"}
    services["db"].get_account_by_topic.return_value = {"id": 1}
    services["db"].get_company_profile.return_value = {"id": 1, "company_name": "CW"}
    services["db"].get_playbooks.return_value = [
        {"id": 1, "trigger_description": "boleto", "auto_respond": True, "active": True},
    ]
    await handle_command(msg, services)
    services["telegram"].send_text.assert_called_once()
    call_text = services["telegram"].send_text.call_args[0][1]
    assert "boleto" in call_text


@pytest.mark.asyncio
async def test_config_playbook_delete_with_ownership():
    from orchestrator.handlers.telegram_commands import handle_command
    services = _make_services()
    msg = {"chat": {"id": 100}, "from": {"id": 42}, "text": "/config_playbook_delete 1"}
    services["db"].get_account_by_topic.return_value = {"id": 1}
    services["db"].get_company_profile.return_value = {"id": 5, "company_name": "CW"}
    services["db"].delete_playbook_owned.return_value = True
    await handle_command(msg, services)
    services["db"].delete_playbook_owned.assert_called_once_with(1, 5)


@pytest.mark.asyncio
async def test_config_playbook_delete_wrong_owner():
    """Deleting a playbook that belongs to another company should fail."""
    from orchestrator.handlers.telegram_commands import handle_command
    services = _make_services()
    msg = {"chat": {"id": 100}, "from": {"id": 42}, "text": "/config_playbook_delete 99"}
    services["db"].get_account_by_topic.return_value = {"id": 1}
    services["db"].get_company_profile.return_value = {"id": 5, "company_name": "CW"}
    services["db"].delete_playbook_owned.return_value = False  # not owned
    await handle_command(msg, services)
    services["db"].delete_playbook_owned.assert_called_once_with(99, 5)
    call_text = services["telegram"].send_text.call_args[0][1]
    assert "não encontrado" in call_text or "não pertence" in call_text


@pytest.mark.asyncio
async def test_config_identidade_full_flow():
    """Test multi-step identity config conversation end-to-end."""
    from orchestrator.handlers.telegram_commands import handle_config_response
    services = _make_services()
    services["db"].update_pending_state.return_value = None
    services["db"].upsert_company_profile.return_value = 1
    services["db"].delete_pending_action.return_value = None

    # Step 1: company_name response
    pending = {"id": 1, "account_id": 1, "action_type": "config_identidade", "state": '{"step": "company_name"}'}
    msg = {"chat": {"id": 100}, "text": "CodeWave"}
    await handle_config_response(msg, pending, services)
    services["db"].update_pending_state.assert_called_once()

    # Step 4: signature response (final step)
    services["db"].update_pending_state.reset_mock()
    pending_final = {"id": 1, "account_id": 1, "action_type": "config_identidade",
                     "state": '{"step": "signature", "company_name": "CodeWave", "cnpj": null, "tone": "formal"}'}
    msg_final = {"chat": {"id": 100}, "text": "Att, Equipe CodeWave"}
    await handle_config_response(msg_final, pending_final, services)
    services["db"].upsert_company_profile.assert_called_once()
    services["db"].delete_pending_action.assert_called_once()


@pytest.mark.asyncio
async def test_config_playbook_full_flow():
    """Test multi-step playbook creation conversation."""
    from orchestrator.handlers.telegram_commands import handle_config_response
    services = _make_services()
    services["db"].update_pending_state.return_value = None
    services["db"].get_company_profile.return_value = {"id": 1, "company_name": "CW"}
    services["db"].create_playbook.return_value = 1
    services["db"].delete_pending_action.return_value = None

    # Final step: auto response
    pending = {"id": 1, "account_id": 1, "action_type": "config_playbook",
               "state": '{"step": "auto", "trigger": "boleto", "template": "Prezado..."}'}
    msg = {"chat": {"id": 100}, "text": "sim"}
    await handle_config_response(msg, pending, services)
    services["db"].create_playbook.assert_called_once()
