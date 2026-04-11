# Plano de Implementação - Email Agent Botões

## Gaps Identificados

### Prioridade ALTA (confirmações de segurança)

1. **Confirmações para ações de risco médio**
   - ✉️ Enviar rascunho: [✅ Confirmar] [❌ Cancelar]
   - 🔇 Silenciar: [✅ Silenciar] [❌ Cancelar]
   - 🗑️ Spam: [✅ Confirmar] [❌ Cancelar]

2. **Editar mensagem após ação**
   - Adicionar "✅ Respondido em DD/MM às HH:MM"
   - Adicionar "✅ Arquivado em DD/MM às HH:MM"
   - Desabilitar botões (remover inline_keyboard)

### Prioridade MÉDIA (fluxos interativos)

3. **💬 Responder Custom**
   - Detectar callback `custom_reply`
   - Guardar estado em pending_replies.json
   - Pedir instrução do usuário
   - Enviar pro GLM-5 Turbo (OpenRouter)
   - Mostrar rascunho com [✉️ Enviar este] [✏️ Ajustar de novo]

4. **🔄 Reclassificar com reprocessamento**
   - Depois de escolher urgência, reprocessar email com LLM
   - Gerar novo resumo e rascunho
   - Atualizar mensagem completa (não só o header)

### Prioridade BAIXA (melhorias)

5. **📝 Criar tarefa melhorado**
   - Título: "[CRITICAL] Assunto do email"
   - Prioridade baseada na urgência (critical = P1, high = P2, etc)

6. **Check de blacklist antes de notificar**
   - No email_processor.py, verificar is_blacklisted(sender)
   - Se blacklist, pular notificação

---

## Estrutura de Arquivos

```
/opt/email-agent/
├── telegram_poller.py        # Principal - processa callbacks
├── vip_manager.py            # ✅ Já implementado
├── pending_replies.json      # Estado de custom replies
├── vip-list.json             # ✅ Já existe
├── blacklist.json            # ✅ Já existe
├── feedback.json             # ✅ Já existe
└── llm_service.py            # Para reprocessar/generar respostas
```

---

## Callbacks a Implementar

### Confirmações (2-passos)

```python
# Fluxo de confirmação
"send_draft" → mostra [✅ Confirmar] [❌ Cancelar]
"confirm_send_draft" → executa envio real
"cancel_send_draft" → cancela

"silence" → mostra [✅ Silenciar] [❌ Cancelar]
"confirm_silence" → adiciona blacklist
"cancel_silence" → cancela

"spam" → mostra [✅ Confirmar] [❌ Cancelar]
"confirm_spam" → marca spam + blacklist
"cancel_spam" → cancela
```

### Responder Custom

```python
"custom_reply" → guarda estado, pede instrução
# Usuário digita mensagem normal (não callback)
# Sistema detecta que há pending custom_reply
# Gera resposta via LLM
# Mostra com [✉️ Enviar este] [✏️ Ajustar de novo]
"send_custom_draft" → envia
"adjust_custom_draft" → pede nova instrução
```

---

## API OpenRouter para LLM

```python
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = "z-ai/glm-5-turbo"

async def generate_custom_reply(email_content: str, instruction: str) -> str:
    """Gera resposta customizada via GLM-5 Turbo"""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            json={
                "model": "z-ai/glm-5-turbo",
                "messages": [
                    {"role": "system", "content": "Você é um assistente que escreve respostas de email profissionais em português."},
                    {"role": "user", "content": f"Email original:\n{email_content}\n\nInstrução: {instruction}\n\nEscreva uma resposta profissional:"}
                ],
                "max_tokens": 1000
            }
        )
        return response.json()["choices"][0]["message"]["content"]
```

---

## Tarefas para Subagentes

### Agente 1: Confirmações e Edição de Mensagem
- Implementar fluxo de confirmação para send_draft, silence, spam
- Implementar edit_message com status
- Implementar disable_buttons

### Agente 2: Responder Custom
- Implementar fluxo interativo completo
- Integração com OpenRouter/GLM-5 Turbo
- Gerenciar pending_replies.json

### Agente 3: Reprocessamento na Reclassificação
- Chamar LLM para gerar novo resumo/rascunho
- Atualizar mensagem completa