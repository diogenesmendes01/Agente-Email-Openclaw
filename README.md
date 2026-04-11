# Agente-Email-OpenClaw

Sistema de automação de emails que recebe webhooks do Gmail via Pub/Sub, processa com LLM e envia mensagens formatadas no Telegram com botões de ação (Arquivar, VIP, Spam).

## Arquitetura

```
Gmail → Pub/Sub → Tailscale Funnel (HTTPS) → Gog (porta 8788) → Orchestrator (porta 8787) → Telegram
```

## Componentes

| Arquivo | Descrição |
|---------|-----------|
| `orchestrator/main.py` | FastAPI app que recebe webhooks e coordena o processamento |
| `orchestrator/handlers/email_processor.py` | Processa emails usando LLM |
| `orchestrator/services/telegram_service.py` | Envia mensagens formatadas no Telegram |
| `orchestrator/services/llm_service.py` | Integração com LLM (OpenAI/GLM) |
| `orchestrator/services/notion_service.py` | Integração com Notion |
| `orchestrator/services/qdrant_service.py` | Vector store para busca |
| `orchestrator/services/gog_service.py` | Integração com Gog CLI |
| `telegram_poller.py` | Bot que fica em loop verificando callbacks (botões clicados) |
| `vip_manager.py` | Gerencia lista VIP e ações |

## Quick Start

### 1. Instalar dependências
```bash
pip install -r requirements.txt
```

### 2. Configurar variáveis de ambiente
```bash
export TELEGRAM_BOT_TOKEN="seu_token_aqui"
export OPENAI_API_KEY="sua_chave_aqui"
```

### 3. Setup Gmail Pub/Sub
```bash
gog gmail watch start --account seu@email.com --label INBOX --topic projects/seu-projeto/topics/gmail-watch
```

### 4. Rodar os serviços
```bash
# Terminal 1: Gog (Pub/Sub listener)
gog gmail watch serve --account seu@email.com --bind 127.0.0.1 --port 8788 \
  --path /gmail-pubsub \
  --hook-url "http://127.0.0.1:8787/hooks/gmail?token=seu_token" \
  --token seu_gog_token

# Terminal 2: Orchestrator
cd orchestrator
PYTHONPATH=/path/do/projeto python3 -m uvicorn orchestrator.main:app --host 127.0.0.1 --port 8787

# Terminal 3: Telegram Poller
python3 telegram_poller.py
```

## Fluxo

1. **Email chega** no Gmail
2. **Pub/Sub** notifica via push (Tailscale Funnel expõe URL pública)
3. **Gog** recebe a notificação e forward pro Orchestrator
4. **Orchestrator** processa email com LLM
5. **Telegram** recebe mensagem formatada com botões (Arquivar, VIP, Spam)
6. **Usuário clica botão** → Telegram Poller processa a ação

## Configuração

### Tailscale Funnel (expor webhook públicamente)
```bash
tailscale funnel --bg http://127.0.0.1:8788
```

### Gmail Watch Topic
```bash
gcloud pubsub topics create gmail-watch --project=seu-projeto
gcloud pubsub subscriptions create gmail-watch-sub --topic=gmail-watch --push-endpoint=https://sua-url.ts.net/gmail-pubsub
```

## Config (config.json)

```json
{
  "telegram_bot_token": "token_do_bot",
  "gmail_account": "seu@email.com",
  "gmail_topic": "projects/seu-projeto/topics/gmail-watch"
}
```
