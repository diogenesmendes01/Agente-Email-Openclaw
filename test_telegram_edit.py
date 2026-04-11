#!/usr/bin/env python3
"""Testa funções de edição de mensagens do Telegram"""

import asyncio
import os
import sys
from datetime import datetime

# Adiciona path do projeto
sys.path.insert(0, "/opt/email-agent")

from orchestrator.services.telegram_service import TelegramService


async def test_edit_functions():
    """Testa as funções de edição"""
    
    service = TelegramService()
    
    if not service._configured:
        print("❌ Telegram não configurado. Configure TELEGRAM_BOT_TOKEN")
        return
    
    print("✅ Telegram configurado")
    print(f"   Chat ID: {service.chat_id}")
    print(f"   API Base: {service.api_base[:50]}...")
    
    # 1. Enviar mensagem de teste
    print("\n📤 Enviando mensagem de teste...")
    
    test_text = """<b>🟠 ALTA │ 📧 Outro │ 85%</b>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📨 Teste do Sistema
📋 Teste de Edição de Mensagens

📝 Esta é uma mensagem de teste para verificar as funções de edição.

🕐 2026-04-10 00:37"""
    
    message_id = await service.send_email_notification(
        email={
            "id": "test123",
            "from_name": "Teste do Sistema",
            "from": "test@example.com",
            "subject": "Teste de Edição de Mensagens",
            "date": "2026-04-10T00:37:00"
        },
        classification={"prioridade": "alta", "categoria": "outro", "confianca": 0.85},
        summary={"resumo": "Esta é uma mensagem de teste para verificar as funções de edição."},
        action={"rascunho_resposta": "", "justificativa": "Testando funções de edição"},
        topic_id=11
    )
    
    if not message_id:
        print("❌ Falha ao enviar mensagem de teste")
        return
    
    print(f"✅ Mensagem enviada: message_id={message_id}")
    
    # Aguardar um pouco
    await asyncio.sleep(2)
    
    # 2. Testar update_message_status
    print("\n🔄 Testando update_message_status...")
    
    now = datetime.now().strftime("%d/%m às %H:%M")
    status = f"✅ Respondido em {now}"
    
    success = await service.update_message_status(
        message_id=message_id,
        status=status,
        original_text=test_text
    )
    
    if success:
        print(f"✅ Status atualizado: {status}")
    else:
        print("❌ Falha ao atualizar status")
    
    await asyncio.sleep(2)
    
    # 3. Testar disable_buttons
    print("\n🔄 Testando disable_buttons...")
    
    success = await service.disable_buttons(message_id=message_id)
    
    if success:
        print("✅ Botões removidos")
    else:
        print("❌ Falha ao remover botões")
    
    print("\n✅ Testes concluídos!")


if __name__ == "__main__":
    asyncio.run(test_edit_functions())