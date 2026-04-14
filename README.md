# Agente-Email-OpenClaw

Sistema inteligente de automacao de emails com IA que classifica, resume, decide acoes e **aprende** com o feedback do usuario. Recebe webhooks do Gmail via Pub/Sub, processa com LLM (OpenRouter + reasoning tokens), armazena memoria vetorial no Qdrant, persiste dados no PostgreSQL e envia notificacoes formatadas no Telegram com botoes de acao interativos.

## Quick Start

### 1. Instalar dependencias do sistema (Ubuntu/Debian)

```bash
apt update && apt install -y python3 python3-pip python3-venv python-is-python3 git
```

### 2. Clonar e rodar o wizard

```bash
git clone https://github.com/diogenesmendes01/Agente-Email-Openclaw.git
cd Agente-Email-Openclaw
python setup_wizard.py
```

O wizard cria um ambiente virtual (`.venv/`) automaticamente, instala as dependencias Python e guia voce por todas as etapas de configuracao interativamente (PostgreSQL, Telegram, Gmail OAuth, playbooks).

## Arquitetura

```
Gmail --> Pub/Sub --> Tailscale Funnel (HTTPS) --> Orchestrator (porta 8787)
                                                        |
                                     +------------------+------------------+
                                     |                  |                  |
                                 PostgreSQL          Qdrant            Telegram
                               (accounts,       (embeddings,        (notificacoes,
                                decisions,       regras aprendidas,  feedback,
                                tasks, metrics,  sender profiles)    acoes, /config)
                                company profiles,
                                playbooks)
                                     |
                                Gmail API
                           (fetch, reply, archive
                            via OAuth direto)
```

### Containers (Docker Compose)

| Container | Imagem | Porta | Funcao |
|-----------|--------|-------|--------|
| `postgres` | `postgres:16-alpine` | 5432 | Banco relacional principal |
| `qdrant` | `qdrant/qdrant` | 6333 | Vector database |
| `orchestrator` | Build local | 8787 | FastAPI app + background workers |

## Features

### Classificacao Inteligente
- Classificacao por importancia, prioridade (Alta/Media/Baixa) e categoria (cliente/financeiro/pessoal/trabalho/etc.)
- Deteccao de entidades (cliente, projeto, prazo, protocolo)
- Busca de emails similares via embeddings (OpenAI text-embedding-3-small)
- Contexto de thread (emails anteriores da conversa)
- Extracao automatica de texto de anexos PDF (com fallback via vision model)

### Playbooks Multi-Empresa
- **Playbooks com auto-resposta**: defina gatilhos ("duvida sobre boleto") e templates de resposta
- O LLM decide qual playbook se aplica ao email recebido (chamada unica)
- Respostas automaticas personalizadas com tom e assinatura da empresa
- Configuravel via Telegram (`/config_playbook`) ou importacao YAML
- Suporte a **multiplas empresas/contas de email** com perfis independentes

### Company Profiles (Perfis Empresariais)
- Perfil por empresa: nome, CNPJ, tom de comunicacao, assinatura
- Clientes cadastrados com contatos, projetos ativos e prioridade
- Regras de dominio manuais (ex: `@pagar.me` = financeiro, prioridade Alta)
- Cross-reference automatico: identifica se o remetente e um cliente conhecido
- Configuravel via Telegram (`/config_identidade`)

### Learning Engine (Motor de Aprendizado)
O agente melhora com o tempo a partir do feedback do usuario:

1. **Regras por remetente**: "emails do joao@xyz.com devem ser Alta prioridade" (3+ correcoes)
2. **Regras por dominio**: "emails de @pagar.me sao financeiro" (3+ correcoes de senders diferentes)
3. **Regras por keyword**: "emails com 'contrato' no assunto devem ser Alta" (com filtro de stopwords PT-BR)

- Minimo de 3 evidencias + 70% confianca para criar regra
- Auto-limpeza de regras com confianca < 50%
- Dispara a cada 50 emails processados (configuravel via `LEARNING_INTERVAL`)
- Precedencia: regras manuais > regras aprendidas > LLM default
- Notificacao no Telegram quando novas regras sao aprendidas

### Acoes via Telegram (Webhook)
O Telegram usa **webhook** (nao long-polling) com confirmacao para acoes perigosas:

- Enviar rascunho de resposta (gerado pelo LLM)
- Criar tarefa com detalhes customizados
- Arquivar email (com confirmacao)
- Adicionar remetente como VIP (com confirmacao)
- Reclassificar urgencia (com feedback estruturado para Qdrant)
- Resposta customizada via LLM (com preview e ajuste)
- Silenciar remetente / marcar como spam (com confirmacao)
- Link direto para o Gmail

### Comandos de Configuracao (Telegram)
- `/config_identidade` - Configura nome, CNPJ, tom e assinatura da empresa (conversacional)
- `/config_playbook` - Cria novo playbook com gatilho, template e modo (auto/manual)
- `/config_playbook_list` - Lista playbooks ativos
- `/config_playbook_delete <id>` - Remove playbook
- `/help_config` - Lista todos os comandos disponiveis

### Observabilidade e Resiliencia
- **Metricas**: `metrics` table com contadores por evento, servico e conta
- **Alertas**: DM no Telegram para erros criticos (com throttle de 15 min)
- **Job Queue**: fila de retry com backoff exponencial (max 5 tentativas)
- **Dead Letter Queue**: jobs que excedem tentativas sao marcados como "dead"
- **Request ID**: cada request recebe UUID unico rastreavel nos logs
- **Rate Limiting**: 30 webhooks/min, 60 callbacks/min (via slowapi)
- **Health Check**: `/health` retorna status de todos os servicos + fila

---

## Guia de Setup Completo (do zero)

### Pre-requisitos

#### Pacotes do sistema (Ubuntu/Debian)

```bash
apt update && apt install -y python3 python3-pip python3-venv python-is-python3 git curl
```

> **Nota:** O wizard (`setup_wizard.py`) instala automaticamente as dependencias Python (`requirements.txt`), incluindo `rich`, `python-dotenv`, `psycopg2-binary`, `requests`, etc. Voce nao precisa rodar `pip install` manualmente.

#### Servicos externos

| Ferramenta | Para que serve | Como instalar |
|------------|---------------|---------------|
| **Python 3.11+** | Rodar o agente | `apt install python3` ou [python.org/downloads](https://www.python.org/downloads/) |
| **PostgreSQL 14+** | Banco relacional principal | `apt install postgresql` ou via Docker |
| **Docker + Docker Compose** (opcional) | Rodar PostgreSQL, Qdrant e o orchestrator em containers | [docs.docker.com/get-docker](https://docs.docker.com/get-docker/) |
| **Google Cloud CLI (gcloud)** | Configurar Gmail API e Pub/Sub | [cloud.google.com/sdk/docs/install](https://cloud.google.com/sdk/docs/install) |
| **Tailscale** | Expor webhook via HTTPS (Funnel) | [tailscale.com/download](https://tailscale.com/download) |

> **PostgreSQL:** pode ser instalado direto na VPS (`apt install postgresql`) ou rodado via Docker. O wizard configura o banco independente de como ele foi instalado — basta fornecer a `DATABASE_URL` correta.

### Passo 1: Clonar o repositorio

```bash
git clone https://github.com/diogenesmendes01/Agente-Email-Openclaw.git
cd Agente-Email-Openclaw
```

### Passo 2: Criar o Bot do Telegram

1. Abra o Telegram e busque por **@BotFather**
2. Envie `/newbot`
3. Escolha um nome (ex: "Email Agent") e um username (ex: `email_agent_xyz_bot`)
4. O BotFather vai retornar um **token** no formato `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11` — guarde isso
5. Para obter o **Chat ID**:
   - Crie um grupo no Telegram e adicione o bot
   - Envie uma mensagem qualquer no grupo
   - Acesse `https://api.telegram.org/bot<SEU_TOKEN>/getUpdates` no navegador
   - Procure por `"chat":{"id":-100xxxxxxxxxx}` — esse numero negativo e o seu Chat ID
   - Se quiser usar **Topics** (forum), ative "Topics" nas configuracoes do grupo

### Passo 3: Configurar APIs de IA

#### OpenRouter (LLM principal)
1. Crie uma conta em [openrouter.ai](https://openrouter.ai/)
2. Va em **Keys** e crie uma API key
3. Adicione creditos (o modelo padrao `z-ai/glm-5-turbo` e barato)
4. Copie a key (comeca com `sk-or-v1-`)

#### OpenAI (embeddings)
1. Crie uma conta em [platform.openai.com](https://platform.openai.com/)
2. Va em **API Keys** e crie uma key
3. Adicione creditos (embeddings `text-embedding-3-small` custam ~$0.02/1M tokens)
4. Copie a key (comeca com `sk-`)

### Passo 4: Configurar Google Cloud, Gmail API e OAuth

1. Crie um projeto no [Google Cloud Console](https://console.cloud.google.com/)
2. Ative a **Gmail API** e o **Pub/Sub**:
   ```bash
   gcloud services enable gmail.googleapis.com --project=SEU_PROJETO
   gcloud services enable pubsub.googleapis.com --project=SEU_PROJETO
   ```
3. Crie **credenciais OAuth 2.0**:
   - Va em **APIs & Services > Credentials > Create Credentials > OAuth client ID**
   - Tipo: **Desktop App**
   - Baixe o JSON e salve como `credentials/client_secret.json` no projeto
4. Configure a **tela de consentimento** (OAuth consent screen):
   - Tipo: **External** (ou Internal se for Google Workspace)
   - Adicione os escopos: `gmail.readonly`, `gmail.modify`, `gmail.compose`
   - Adicione seu email como **Test user** (enquanto o app estiver em modo de teste)
5. Crie o **topic** do Pub/Sub:
   ```bash
   gcloud pubsub topics create gmail-watch --project=SEU_PROJETO
   ```
6. De permissao para o Gmail publicar no topic:
   ```bash
   gcloud pubsub topics add-iam-policy-binding gmail-watch \
     --project=SEU_PROJETO \
     --member="serviceAccount:gmail-api-push@system.gserviceaccount.com" \
     --role="roles/pubsub.publisher"
   ```

### Passo 5: Autenticar conta Gmail

O agente usa a Gmail API diretamente (sem ferramentas externas). Autentique cada conta:

```bash
pip install -r requirements.txt
python scripts/gmail_auth.py --account seu@email.com
```

- O navegador abre para voce autorizar o acesso
- O token e salvo em `credentials/token_seu@email.com.json`
- Para adicionar mais contas, repita o comando com outro email

7. Gere um token para o webhook (qualquer string aleatoria):
   ```bash
   # Linux/Mac:
   openssl rand -hex 32
   # Windows (PowerShell):
   -join ((1..32) | ForEach-Object { '{0:x}' -f (Get-Random -Max 16) })
   ```

### Passo 6: Configurar variaveis de ambiente

```bash
cp .env.example .env
```

Edite o `.env`:

```env
# LLM
OPENROUTER_API_KEY=sk-or-v1-xxxxx
OPENAI_API_KEY=sk-xxxxx

# Telegram
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_CHAT_ID=-100xxxxxxxxxx
TELEGRAM_WEBHOOK_SECRET=qualquer_string_aleatoria
TELEGRAM_ALLOWED_USER_IDS=123456789
TELEGRAM_ALERT_USER_ID=123456789

# PostgreSQL
POSTGRES_PASSWORD=senha_segura
DATABASE_URL=postgresql://emailagent:senha_segura@postgres:5432/emailagent

# Gmail (multiplas contas: GMAIL_ACCOUNT_1, GMAIL_HOOK_TOKEN_1, etc.)
GMAIL_ACCOUNT_1=seu@email.com
GMAIL_HOOK_TOKEN_1=seu_token_hex

# Tailscale
FUNNEL_BASE_URL=https://sua-maquina.tail-xxxxx.ts.net

# Qdrant (nao altere se estiver usando Docker Compose)
QDRANT_HOST=qdrant
QDRANT_PORT=6333

# Opcional
LLM_MODEL=z-ai/glm-5-turbo
LLM_VISION_MODEL=google/gemini-2.5-flash
LEARNING_INTERVAL=50
METRICS_RETENTION_DAYS=90
ALERT_THROTTLE_MINUTES=15
JOB_MAX_ATTEMPTS=5
```

### Passo 7: Subir os servicos

```bash
docker-compose up -d
```

Isso inicia 3 containers:
- **postgres** — PostgreSQL 16 (porta 5432, schema automatico via init script)
- **qdrant** — Vector database (porta 6333)
- **orchestrator** — FastAPI app + background workers (porta 8787)

O orchestrator registra o webhook do Telegram automaticamente no startup.

Verifique:
```bash
docker-compose ps
docker-compose logs -f  # logs em tempo real
curl http://localhost:8787/health
```

#### Rodar manualmente (desenvolvimento)

```bash
pip install -r requirements.txt

# Terminal 1: PostgreSQL + Qdrant
docker-compose up postgres qdrant

# Terminal 2: Orchestrator
DATABASE_URL=postgresql://emailagent:senha@localhost:5432/emailagent \
  uvicorn orchestrator.main:app --host 127.0.0.1 --port 8787
```

### Passo 8: Configurar Tailscale Funnel

```bash
tailscale up
tailscale funnel --bg http://127.0.0.1:8787
```

Anote a URL gerada (formato: `https://sua-maquina.tail-xxxxx.ts.net`).

### Passo 9: Configurar Gmail Watch (Pub/Sub)

1. Crie a subscription apontando para o Tailscale Funnel:
   ```bash
   gcloud pubsub subscriptions create gmail-watch-sub \
     --topic=gmail-watch \
     --project=SEU_PROJETO \
     --push-endpoint="https://sua-maquina.tail-xxxxx.ts.net/hooks/gmail?token=SEU_TOKEN"
   ```

2. Ative o Gmail Watch:
   ```bash
   python scripts/gmail_watch.py \
     --account seu@email.com \
     --topic projects/SEU_PROJETO/topics/gmail-watch
   ```

> **Importante:** O Gmail Watch expira a cada 7 dias. Configure um cron para renovar:
> ```bash
> 0 0 */6 * * cd /caminho/do/projeto && python scripts/gmail_watch.py --account seu@email.com --topic projects/SEU_PROJETO/topics/gmail-watch
> ```

### Passo 10: Configurar Playbooks (opcional)

#### Via Telegram (interativo)
```
/config_identidade    # configura empresa
/config_playbook      # cria playbook passo a passo
```

#### Via YAML (bulk import)
```bash
cp playbooks/modelo.yaml.example playbooks/minha-empresa.yaml
# Edite o arquivo com seus dados
python scripts/import_playbooks.py playbooks/minha-empresa.yaml --account-id 1
```

### Passo 11: Testar

1. **Health check:**
   ```bash
   curl http://localhost:8787/health
   # {"status":"healthy","services":{"postgres":"connected","qdrant":"connected",...},"queue":{"pending_jobs":0}}
   ```

2. **Enviar email de teste** e verificar:
   - Logs: `docker-compose logs -f orchestrator`
   - Notificacao no Telegram com botoes de acao

3. **Teste manual via API:**
   ```bash
   curl -X POST http://localhost:8787/hooks/gmail/test \
     -H "Content-Type: application/json" \
     -d '{"emailId":"SEU_EMAIL_ID","account":"seu@email.com"}'
   ```

---

## Pipeline de Processamento

```
 1. Webhook recebido (Gmail Pub/Sub)
 2. Deduplicacao (cache LRU in-memory, 1000 entradas)
 3. Fetch email via Gmail API (async)
 4. Parse + limpeza + extracao de PDF (se houver anexo)
 5. Buscar contexto:
    a. Config da conta (PostgreSQL)
    b. Company profile + playbooks (PostgreSQL)
    c. Emails similares (Qdrant, via embedding)
    d. Sender profile com padroes de correcao (Qdrant)
    e. Regras aprendidas (Qdrant)
 6. Classificar com LLM (prompt enriquecido, max 6000 tokens)
 7. Check playbooks: se match com auto_respond, gerar e enviar resposta
 8. Resumir com LLM
 9. Decidir acao com LLM (com tom/assinatura da empresa)
10. Persistir decisao (PostgreSQL + Qdrant)
11. Executar acao (arquivar/criar task/rascunho)
12. Notificar Telegram com botoes interativos
13. A cada N emails: disparar ciclo de aprendizado
14. Metricas + retry em caso de falha
```

## Testes

```bash
# Rodar todos os testes (195 testes)
python -m pytest tests/ -v

# Testes especificos
python -m pytest tests/test_playbook_service.py -v
python -m pytest tests/test_telegram_commands.py -v
python -m pytest tests/test_telegram_callbacks.py -v
python -m pytest tests/test_actions.py -v
python -m pytest tests/test_learning_engine.py -v
python -m pytest tests/test_email_processor_playbooks.py -v
```

## Estrutura do Projeto

```
Agente-Email-Openclaw/
+-- orchestrator/
|   +-- main.py                              # FastAPI app v3.0 + background workers
|   +-- settings.py                          # Settings unificado (env validation)
|   +-- middleware/
|   |   +-- request_id.py                    # Request ID via contextvars
|   +-- handlers/
|   |   +-- email_processor.py               # Pipeline principal (com playbooks)
|   |   +-- telegram_callbacks.py            # Router de callbacks + text messages
|   |   +-- telegram_commands.py             # /config_* commands (conversacional)
|   +-- services/
|   |   +-- database_service.py              # PostgreSQL (accounts, decisions, tasks, playbooks)
|   |   +-- gmail_service.py                 # Gmail API direta (OAuth)
|   |   +-- llm_service.py                   # LLM (OpenRouter + OpenAI + playbook matching)
|   |   +-- qdrant_service.py                # Vector DB (embeddings, rules, profiles)
|   |   +-- telegram_service.py              # Telegram (webhook, notificacoes, callbacks)
|   |   +-- playbook_service.py              # Match + auto-respond de playbooks
|   |   +-- learning_engine.py               # Motor de aprendizado automatico
|   |   +-- metrics_service.py               # Metricas e contadores
|   |   +-- alert_service.py                 # Alertas criticos via DM
|   |   +-- job_queue.py                     # Fila de retry com dead letter
|   +-- actions/                             # Modulos de acao (execute pattern)
|   |   +-- archive.py                       # Arquivar email
|   |   +-- vip.py                           # Adicionar VIP
|   |   +-- silence.py                       # Silenciar remetente
|   |   +-- spam.py                          # Marcar spam + blacklist
|   |   +-- task.py                          # Criar tarefa
|   |   +-- feedback.py                      # Reclassificar urgencia
|   |   +-- reply.py                         # Resposta customizada via LLM
|   +-- utils/
|       +-- email_parser.py                  # Parsing de emails
|       +-- text_cleaner.py                  # Limpeza de texto
|       +-- pdf_reader.py                    # Extracao de texto de PDFs
+-- sql/
|   +-- schema.sql                           # Schema PostgreSQL (14 tabelas)
|   +-- migrations/
|       +-- 001_phase3_4_tables.sql          # Migração Phase 3+4 (bancos existentes)
|       +-- 002_idempotency_constraints.sql  # Dedup + UNIQUE constraints
+-- setup_wizard.py                              # Wizard de setup interativo
+-- setup_steps/                                 # Modulos do wizard
|   +-- common.py                                # UI helpers (rich, prompts)
|   +-- dependencies.py                          # Instalar requirements.txt
|   +-- env_config.py                            # Gerar .env interativamente
|   +-- database.py                              # Criar banco + rodar migrations
|   +-- telegram.py                              # Validar bot + descobrir chat_id
|   +-- gmail.py                                 # OAuth + Gmail Watch
|   +-- accounts.py                              # Criar contas no banco
|   +-- playbooks.py                             # Importar playbooks de YAML
+-- tests/                                   # 195 testes
|   +-- conftest.py                          # Fixtures compartilhados
|   +-- test_database_service.py             # 15 testes
|   +-- test_telegram_service.py             # 5 testes
|   +-- test_actions.py                      # 11 testes
|   +-- test_telegram_callbacks.py           # 7 testes
|   +-- test_telegram_commands.py            # 6 testes
|   +-- test_playbook_service.py             # 5 testes
|   +-- test_email_processor_playbooks.py    # 2 testes
|   +-- test_enriched_prompts.py             # 7 testes
|   +-- test_learning_engine.py              # 5 testes
|   +-- test_qdrant_extensions.py            # 8 testes
|   +-- test_settings.py                     # 3 testes
|   +-- test_pdf_reader.py                   # 5 testes
|   +-- test_gmail_attachments.py            # 3 testes
|   +-- test_metrics_service.py              # 4 testes
|   +-- test_alert_service.py                # 3 testes
|   +-- test_job_queue.py                    # 5 testes
|   +-- test_retry.py                        # 2 testes
|   +-- test_request_id.py                   # 2 testes
+-- playbooks/
|   +-- modelo.yaml.example                  # Exemplo de YAML para import
+-- scripts/
|   +-- gmail_auth.py                        # Setup OAuth para contas Gmail
|   +-- gmail_watch.py                       # Ativar Gmail Watch (Pub/Sub)
|   +-- import_playbooks.py                  # Importar playbooks de YAML
|   +-- migrate_feedback.py                  # Migracao feedback.json -> Qdrant
+-- credentials/                             # Tokens OAuth (nao commitado)
+-- docker-compose.yml                       # 3 containers (postgres, qdrant, orchestrator)
+-- Dockerfile                               # Python 3.11-slim
+-- requirements.txt                         # Dependencias
+-- .env.example                             # Template de variaveis
```

## Schema PostgreSQL

O banco possui 13 tabelas, criadas automaticamente pelo `sql/schema.sql`:

| Tabela | Descricao |
|--------|-----------|
| `accounts` | Contas Gmail com config (VIPs, telegram_topic) |
| `vip_list` | Lista de remetentes VIP por conta |
| `blacklist` | Remetentes bloqueados |
| `feedback` | Feedback do usuario (correcoes de classificacao) |
| `decisions` | Log de todas as decisoes tomadas |
| `tasks` | Tarefas criadas a partir de emails |
| `history_ids` | Controle de history IDs do Gmail |
| `metrics` | Metricas de uso (tokens, latencia, erros) |
| `failed_jobs` | Fila de retry com tentativas e dead letter |
| `pending_actions` | Acoes pendentes de confirmacao (TTL 10 min, isolada por topic_id) |
| `company_profiles` | Perfis empresariais (nome, tom, assinatura) |
| `clients` | Clientes cadastrados por empresa |
| `domain_rules` | Regras de dominio manuais |
| `playbooks` | Playbooks com gatilho, template e auto-respond |

### Migracoes (bancos existentes)

Se o banco ja existe com tabelas de versoes anteriores, rode as migracoes:

```bash
# Phase 3+4: tabelas de pending_actions, company_profiles, playbooks, etc.
psql $DATABASE_URL -f sql/migrations/001_phase3_4_tables.sql

# Idempotencia: dedup de decisions/playbooks + constraints UNIQUE + NOT NULL
psql $DATABASE_URL -f sql/migrations/002_idempotency_constraints.sql
```

Os scripts sao idempotentes (`IF NOT EXISTS`, dedup antes de criar constraints), entao e seguro rodar mais de uma vez. O `setup_wizard.py` aplica todas as migracoes automaticamente.

## Troubleshooting

### O bot do Telegram nao responde
- Verifique os logs: `docker-compose logs orchestrator | grep Telegram`
- Confirme que `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID` estao corretos
- O bot precisa ser **admin do grupo** para ler mensagens
- Verifique se o webhook foi registrado: `curl https://api.telegram.org/bot<TOKEN>/getWebhookInfo`

### Webhook nao recebe emails
- Verifique se o Tailscale Funnel esta ativo: `tailscale funnel status`
- Verifique se o Gmail Watch esta ativo (expira a cada 7 dias)
- Confira os logs: `docker-compose logs -f orchestrator`

### Gmail retorna "not_ready" no health check
- Verifique se existe o diretorio `credentials/` com os tokens
- Execute `python scripts/gmail_auth.py --account seu@email.com` para re-autenticar

### PostgreSQL retorna "disconnected"
- Verifique se o container esta rodando: `docker-compose ps postgres`
- Confirme que `DATABASE_URL` esta correto no `.env`
- Verifique os logs: `docker-compose logs postgres`

### Qdrant retorna "disconnected"
- Verifique se o container esta rodando: `docker-compose ps qdrant`
- No Docker Compose, o host e `qdrant` (nao `localhost`)

### Jobs na dead letter queue
- Verifique: `curl http://localhost:8787/health` (campo `queue.dead_jobs`)
- Alertas sao enviados via DM para `TELEGRAM_ALERT_USER_ID`
