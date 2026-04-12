# Agente-Email-OpenClaw

Sistema inteligente de automacao de emails com IA que classifica, resume, decide acoes e **aprende** com o feedback do usuario. Recebe webhooks do Gmail via Pub/Sub, processa com LLM (OpenRouter + reasoning tokens), armazena memoria vetorial no Qdrant e envia notificacoes formatadas no Telegram com botoes de acao.

## Arquitetura

```
Gmail → Pub/Sub → Tailscale Funnel (HTTPS) → GOG CLI (porta 8788) → Orchestrator (porta 8787)
                                                                           │
                                                    ┌──────────────────────┼──────────────────────┐
                                                    │                      │                      │
                                                 Notion              Qdrant               Telegram
                                            (config, tasks,     (embeddings,          (notificacoes,
                                             decisoes,           regras aprendidas,     feedback,
                                             company profiles)   sender profiles)       acoes)
```

## Componentes

### Servicos Core

| Arquivo | Descricao |
|---------|-----------|
| `orchestrator/main.py` | FastAPI app com rate limiting, deduplicacao e webhook auth |
| `orchestrator/handlers/email_processor.py` | Pipeline completo: fetch → parse → classify → summarize → action → notify |
| `orchestrator/services/llm_service.py` | LLM via OpenRouter com retry exponencial e reasoning tokens |
| `orchestrator/services/notion_service.py` | Notion API async (config, tarefas, log de decisoes) |
| `orchestrator/services/qdrant_service.py` | Vector DB: embeddings, feedback estruturado, regras aprendidas, sender profiles |
| `orchestrator/services/gog_service.py` | Gmail via GOG CLI (async subprocess) |
| `orchestrator/services/telegram_service.py` | Notificacoes Telegram com split de mensagens longas |

### Novos Componentes

| Arquivo | Descricao |
|---------|-----------|
| `orchestrator/services/company_service.py` | Perfis empresariais do Notion com cache TTL (5 min) |
| `orchestrator/services/learning_engine.py` | Motor de aprendizado: gera regras automaticas a partir de feedback |
| `telegram_poller.py` | Bot de long-polling para callbacks (botoes) com feedback estruturado |
| `vip_manager.py` | Gerencia listas VIP e blacklist |
| `scripts/migrate_feedback.py` | Migracao de feedback.json para Qdrant |

### Utilitarios

| Arquivo | Descricao |
|---------|-----------|
| `orchestrator/utils/email_parser.py` | Parsing de emails (headers, body, attachments) |
| `orchestrator/utils/text_cleaner.py` | Limpeza de HTML, normalizacao de texto |

## Features

### Classificacao Inteligente
- Classificacao por importancia, prioridade (Alta/Media/Baixa) e categoria (cliente/financeiro/pessoal/trabalho/etc.)
- Deteccao de entidades (cliente, projeto, prazo, protocolo)
- Busca de emails similares via embeddings (OpenAI text-embedding-3-small)
- Contexto de thread (emails anteriores da conversa)

### Company Profiles (Perfis Empresariais)
- Suporte a **multiplas empresas/contas de email**
- Clientes cadastrados no Notion com contatos, projetos ativos e prioridade
- Regras de dominio manuais (ex: `@pagar.me` = financeiro, prioridade Alta)
- Tom de comunicacao, assinatura e idioma por empresa
- Cross-reference automatico: identifica se o remetente e um cliente conhecido

### Learning Engine (Motor de Aprendizado)
O agente melhora com o tempo a partir do feedback do usuario:

1. **Regras por remetente**: "emails do joao@xyz.com devem ser Alta prioridade" (3+ correcoes)
2. **Regras por dominio**: "emails de @pagar.me sao financeiro" (3+ correcoes de senders diferentes)
3. **Regras por keyword**: "emails com 'contrato' no assunto devem ser Alta" (com filtro de stopwords PT-BR)

- Minimo de 3 evidencias + 70% confianca para criar regra
- Auto-limpeza de regras com confianca < 50%
- Dispara a cada 50 emails processados (configuravel via `LEARNING_INTERVAL`)
- Precedencia: regras manuais do Notion > regras aprendidas > LLM default
- Notificacao no Telegram quando novas regras sao aprendidas

### Prompts Enriquecidos
Todos os prompts (classificacao, resumo, acao) incluem:
- Contexto da empresa (nome, setor, tom)
- Perfil do remetente (historico, taxa de acerto, padroes de correcao)
- Regras aprendidas e regras de dominio
- Feedback de emails similares (usuario corrigiu X para Y)
- Gerenciamento de tamanho (max 6000 tokens com truncamento inteligente)

### Acoes via Telegram
- Enviar rascunho de resposta
- Criar tarefa no Notion
- Arquivar email
- Adicionar remetente como VIP
- Reclassificar urgencia (com feedback estruturado para Qdrant)
- Resposta customizada via LLM
- Silenciar remetente / marcar como spam
- Link direto para o Gmail

## Quick Start

### 1. Copiar e configurar variaveis de ambiente
```bash
cp .env.example .env
# Editar .env com suas chaves
```

### 2. Instalar dependencias
```bash
pip install -r requirements.txt
```

### 3. Rodar com Docker (recomendado para VPS)
```bash
docker-compose up -d
```

Isso inicia 3 servicos:
- **qdrant** — Vector database (porta 6333)
- **orchestrator** — FastAPI webhook server (porta 8787)
- **telegram-poller** — Bot de long-polling

### 4. Rodar manualmente (desenvolvimento)
```bash
# Terminal 1: Qdrant
docker run -p 6333:6333 qdrant/qdrant

# Terminal 2: GOG (Pub/Sub listener)
gog gmail watch serve --account seu@email.com --bind 127.0.0.1 --port 8788 \
  --path /gmail-pubsub \
  --hook-url "http://127.0.0.1:8787/hooks/gmail?token=$GOG_HOOK_TOKEN_PESSOAL" \
  --token seu_gog_token

# Terminal 3: Orchestrator
uvicorn orchestrator.main:app --host 127.0.0.1 --port 8787

# Terminal 4: Telegram Poller
python telegram_poller.py
```

### 5. Setup Gmail Pub/Sub
```bash
# Criar topic e subscription
gcloud pubsub topics create gmail-watch --project=seu-projeto
gcloud pubsub subscriptions create gmail-watch-sub \
  --topic=gmail-watch \
  --push-endpoint=https://sua-url.ts.net/gmail-pubsub

# Ativar watch
gog gmail watch start --account seu@email.com --label INBOX \
  --topic projects/seu-projeto/topics/gmail-watch
```

### 6. Expor webhook (Tailscale Funnel)
```bash
tailscale funnel --bg http://127.0.0.1:8788
```

## Configuracao

### Variaveis de Ambiente

Veja `.env.example` para a lista completa. As principais:

| Variavel | Descricao |
|----------|-----------|
| `OPENROUTER_API_KEY` | Chave da API OpenRouter (LLM principal) |
| `OPENAI_API_KEY` | Chave da API OpenAI (embeddings) |
| `TELEGRAM_BOT_TOKEN` | Token do bot Telegram |
| `TELEGRAM_CHAT_ID` | ID do chat/grupo Telegram |
| `NOTION_API_KEY` | Chave da integracao Notion |
| `GOG_HOOK_TOKEN_PESSOAL` | Token de autenticacao do webhook GOG |
| `GOG_KEYRING_PASSWORD` | Senha do keyring do GOG |
| `QDRANT_HOST` / `QDRANT_PORT` | Host e porta do Qdrant (default: localhost:6333) |

### Notion Databases

O sistema usa estas databases no Notion:

| Database | Env Var | Descricao |
|----------|---------|-----------|
| Config | `NOTION_DB_CONFIG` | Configuracao por conta (VIPs, palavras urgencia) |
| Tarefas | `NOTION_DB_TAREFAS` | Tarefas criadas a partir de emails |
| Decisoes | `NOTION_DB_DECISOES` | Log de todas as decisoes do agente |
| Company Profiles | `NOTION_DB_COMPANY_PROFILES` | Perfis empresariais (nome, setor, tom, assinatura) |
| Clientes | `NOTION_DB_CLIENTES` | Clientes com contatos e projetos ativos |
| Domain Rules | `NOTION_DB_DOMAIN_RULES` | Regras manuais por dominio de email |

### Company Profiles (opcional)

Para usar o suporte a empresas, crie 3 databases no Notion:

**Company Profiles:**
- Nome (title), Conta Email (email), Setor (select), Tom (select), Assinatura (rich_text), Idioma Padrao (select)

**Clientes:**
- Nome (title), Contatos (rich_text - emails separados por virgula), Projeto Ativo (rich_text), Prioridade (select), Notas (rich_text), Company Profile (relation)

**Domain Rules:**
- Dominio (title - ex: `@pagar.me`), Categoria (select), Prioridade Minima (select), Acao Padrao (select), Company Profile (relation)

## Migracao

Se voce ja tem um `feedback.json` de versoes anteriores, migre para Qdrant:

```bash
python scripts/migrate_feedback.py
```

## Pipeline de Processamento

```
1. Webhook recebido (Gmail via GOG)
2. Deduplicacao (cache in-memory)
3. Fetch email via GOG CLI (async)
4. Parse e limpeza do corpo
5. Buscar contexto:
   a. Config da conta (Notion)
   b. Company profile + clientes + domain rules (Notion, cached)
   c. Emails similares (Qdrant, via embedding)
   d. Sender profile com padroes de correcao (Qdrant)
   e. Regras aprendidas (Qdrant)
6. Classificar com LLM (prompt enriquecido)
7. Resumir com LLM
8. Decidir acao com LLM (com tom/assinatura da empresa)
9. Persistir decisao (Notion + Qdrant)
10. Executar acao (arquivar/criar task/rascunho)
11. Notificar Telegram com botoes
12. A cada N emails: disparar ciclo de aprendizado
```

## Testes

```bash
# Rodar todos os testes
python -m pytest tests/ -v

# Testes especificos
python -m pytest tests/test_company_service.py -v
python -m pytest tests/test_learning_engine.py -v
python -m pytest tests/test_qdrant_extensions.py -v
python -m pytest tests/test_enriched_prompts.py -v
```

## Estrutura do Projeto

```
Agente-Email-Openclaw/
├── orchestrator/
│   ├── main.py                          # FastAPI app
│   ├── handlers/
│   │   └── email_processor.py           # Pipeline principal
│   ├── services/
│   │   ├── company_service.py           # Perfis empresariais (Notion)
│   │   ├── gog_service.py               # Gmail via GOG CLI
│   │   ├── learning_engine.py           # Motor de aprendizado
│   │   ├── llm_service.py               # LLM (OpenRouter + OpenAI)
│   │   ├── notion_service.py            # Notion API
│   │   ├── qdrant_service.py            # Vector DB
│   │   └── telegram_service.py          # Notificacoes Telegram
│   └── utils/
│       ├── email_parser.py              # Parsing de emails
│       └── text_cleaner.py              # Limpeza de texto
├── tests/
│   ├── test_company_service.py          # 6 testes
│   ├── test_enriched_prompts.py         # 7 testes
│   ├── test_learning_engine.py          # 5 testes
│   └── test_qdrant_extensions.py        # 8 testes
├── scripts/
│   └── migrate_feedback.py              # Migracao feedback.json → Qdrant
├── telegram_poller.py                   # Bot de callbacks
├── vip_manager.py                       # Gerencia VIP/blacklist
├── docker-compose.yml                   # 3 servicos (qdrant, orchestrator, poller)
├── Dockerfile                           # Python 3.11-slim
├── requirements.txt                     # Dependencias
├── .env.example                         # Template de variaveis
└── config.json                          # Config de accounts/databases
```
