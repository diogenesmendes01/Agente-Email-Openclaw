# Agente-Email-Openclaw

Sistema de automação de emails que recebe webhooks do Gmail via Pub/Sub, processa com LLM e envia mensagens formatadas no Telegram com botões de ação.

## Arquitetura

```
Gmail → Pub/Sub → Tailscale Funnel → Gog (8788) → Orchestrator (8787) → Telegram
```

## Componentes

### Orchestrator (`orchestrator/`)
- `main.py` - FastAPI app que recebe webhooks e coordena o processamento
- `handlers/email_processor.py` - Processa emails usando LLM
- `services/` - Serviços de integração (Telegram, LLM, Notion, Qdrant, GOG)

### Gog (`telegram_poller.py`)
- Bot que fica em loop verificando callbacks do Telegram (botões clicados)
- Gerencia ações pendentes e confirmações

## Configuração

### Variáveis de ambiente
```bash
TELEGRAM_BOT_TOKEN=       # Token do bot do Telegram
TELEGRAM_BOT_TOKEN_FILE=   # Ou arquivo com o token
OPENAI_API_KEY=           # Chave da OpenAI (opcional)
NOTION_API_KEY=           # Chave do Notion (opcional)
QDRANT_URL=              # URL do Qdrant (default: http://localhost:6333)
```

### Gmail Pub/Sub
```bash
# Setup do watch
gog gmail watch start --account <email> --label INBOX --topic projects/<project>/topics/<topic>
```

### Execução

```bash
# Orchestrator
cd orchestrator
PYTHONPATH=/opt/email-agent python3 -m uvicorn orchestrator.main:app --host 127.0.0.1 --port 8787

# Gog (Pub/Sub listener)
gog gmail watch serve --account <email> --bind 127.0.0.1 --port 8788 --path /gmail-pubsub --hook-url http://127.0.0.1:8787/hooks/gmail
```

## Fluxo

1. Email chega no Gmail
2. Pub/Sub notifica via push (Tailscale Funnel)
3. Gog recebe e forward pro Orchestrator
4. Orchestrator processa email com LLM
5. Envia mensagem formatada no Telegram com botões (Arquivar, VIP, Spam)
6. Usuário clica botão → Telegram Poller processa ação
