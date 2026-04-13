# Agente-Email-OpenClaw

Sistema inteligente de automacao de emails com IA que classifica, resume, decide acoes e **aprende** com o feedback do usuario. Recebe webhooks do Gmail via Pub/Sub, processa com LLM (OpenRouter + reasoning tokens), armazena memoria vetorial no Qdrant e envia notificacoes formatadas no Telegram com botoes de acao.

## Arquitetura

```
Gmail → Pub/Sub → Tailscale Funnel (HTTPS) → Orchestrator (porta 8787)
                                                      │
                                   ┌──────────────────┼──────────────────┐
                                   │                  │                  │
                                Notion             Qdrant            Telegram
                           (config, tasks,    (embeddings,        (notificacoes,
                            decisoes,          regras aprendidas,  feedback,
                            company profiles)  sender profiles)    acoes)
                                   │
                              Gmail API
                         (fetch, reply, archive
                          via OAuth direto)
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
| `orchestrator/services/gmail_service.py` | Gmail API direta com OAuth (sem dependencia externa) |
| `orchestrator/services/telegram_service.py` | Notificacoes Telegram com split de mensagens longas |
| `orchestrator/services/company_service.py` | Perfis empresariais do Notion com cache TTL (5 min) |
| `orchestrator/services/learning_engine.py` | Motor de aprendizado: gera regras automaticas a partir de feedback |
| `telegram_poller.py` | Bot de long-polling para callbacks (botoes) com feedback estruturado |
| `vip_manager.py` | Gerencia listas VIP e blacklist |

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

### Acoes via Telegram
- Enviar rascunho de resposta
- Criar tarefa no Notion
- Arquivar email
- Adicionar remetente como VIP
- Reclassificar urgencia (com feedback estruturado para Qdrant)
- Resposta customizada via LLM
- Silenciar remetente / marcar como spam
- Link direto para o Gmail

---

## Guia de Setup Completo (do zero)

### Pre-requisitos

Antes de comecar, instale:

| Ferramenta | Para que serve | Como instalar |
|------------|---------------|---------------|
| **Python 3.11+** | Rodar o agente | [python.org/downloads](https://www.python.org/downloads/) |
| **Docker + Docker Compose** | Rodar Qdrant e os servicos | [docs.docker.com/get-docker](https://docs.docker.com/get-docker/) |
| **Google Cloud CLI (gcloud)** | Configurar Gmail API e Pub/Sub | [cloud.google.com/sdk/docs/install](https://cloud.google.com/sdk/docs/install) |
| **Tailscale** | Expor webhook via HTTPS (Funnel) | [tailscale.com/download](https://tailscale.com/download) |
| **Git** | Clonar o repositorio | [git-scm.com/downloads](https://git-scm.com/downloads) |

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

### Passo 3: Criar a integracao do Notion

1. Acesse [notion.so/my-integrations](https://www.notion.so/my-integrations)
2. Clique em **"Nova integracao"**
3. De um nome (ex: "Email Agent"), selecione o workspace e clique em **Enviar**
4. Copie o **Internal Integration Secret** (comeca com `secret_`) — guarde isso
5. Crie as seguintes **databases** no Notion:

#### Database: Config
Propriedades:
- `Conta` (title) — email da conta Gmail
- `VIPs` (multi_select) — emails VIP
- `Palavras Urgencia` (multi_select) — palavras que indicam urgencia
- `Telegram Topic` (number) — ID do topic no grupo (opcional)

#### Database: Tarefas
Propriedades:
- `Titulo` (title)
- `Prioridade` (select): Alta, Media, Baixa
- `Status` (select): Pendente, Em Andamento, Concluido
- `Email ID` (rich_text)
- `Conta` (rich_text)

#### Database: Decisoes
Propriedades:
- `Email ID` (title)
- `Conta` (rich_text)
- `Assunto` (rich_text)
- `De` (rich_text)
- `Classificacao` (select)
- `Prioridade` (select)
- `Acao` (select)
- `Resumo` (rich_text)
- `Data` (date)

6. **Compartilhe cada database** com a integracao:
   - Abra cada database → clique nos `...` no canto superior direito → **Conexoes** → selecione sua integracao
7. **Copie o ID de cada database**:
   - Abra a database em pagina cheia
   - A URL tera o formato: `https://notion.so/workspace/XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX?v=...`
   - O `XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX` (32 caracteres hex) e o Database ID
   - Converta para formato UUID: `XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX`

### Passo 4: Configurar APIs de IA

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

### Passo 5: Configurar Google Cloud, Gmail API e OAuth

1. Crie um projeto no [Google Cloud Console](https://console.cloud.google.com/)
2. Ative a **Gmail API** e o **Pub/Sub**:
   ```bash
   gcloud services enable gmail.googleapis.com --project=SEU_PROJETO
   gcloud services enable pubsub.googleapis.com --project=SEU_PROJETO
   ```
3. Crie **credenciais OAuth 2.0**:
   - Va em **APIs & Services → Credentials → Create Credentials → OAuth client ID**
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

### Passo 6: Autenticar conta Gmail

O agente usa a Gmail API diretamente (sem ferramentas externas). Autentique cada conta:

```bash
pip install -r requirements.txt
python scripts/gmail_auth.py --account seu@email.com
```

- O navegador abre para voce autorizar o acesso
- Faca login com a conta `seu@email.com`
- O token e salvo em `credentials/token_seu@email.com.json`
- Para adicionar mais contas, repita o comando com outro email

> **Nota:** O token e renovado automaticamente. Se expirar, rode o script novamente.

7. Gere um token para o webhook (qualquer string aleatoria):
   ```bash
   # Linux/Mac:
   openssl rand -hex 32
   # Windows (PowerShell):
   -join ((1..32) | ForEach-Object { '{0:x}' -f (Get-Random -Max 16) })
   ```
   Guarde esse token — ele sera usado como `GOG_HOOK_TOKEN_PESSOAL` no `.env`

### Passo 7: Configurar variaveis de ambiente

```bash
cp .env.example .env
```

Edite o `.env` com os valores que voce coletou:

```env
# LLM
OPENROUTER_API_KEY=sk-or-v1-xxxxx          # Passo 4
OPENAI_API_KEY=sk-xxxxx                     # Passo 4

# Telegram
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...       # Passo 2
TELEGRAM_CHAT_ID=-100xxxxxxxxxx            # Passo 2
TELEGRAM_WEBHOOK_SECRET=qualquer_string    # gere um valor aleatorio

# Notion
NOTION_API_KEY=secret_xxxxx                # Passo 3
NOTION_DB_CONFIG=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx      # Passo 3
NOTION_DB_TAREFAS=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx     # Passo 3
NOTION_DB_DECISOES=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx    # Passo 3

# Gmail
GOG_HOOK_TOKEN_PESSOAL=seu_token_hex       # Passo 6
GOG_HOOK_ACCOUNT=seu@email.com             # sua conta Gmail

# Qdrant (nao altere se estiver usando Docker Compose)
QDRANT_HOST=localhost
QDRANT_PORT=6333
```

### Passo 8: Configurar config.json

Edite o `config.json` com seus dados reais:

```json
{
  "notion": {
    "api_key_env": "NOTION_API_KEY",
    "databases": {
      "config": "SEU_DB_CONFIG_ID",
      "projetos": "SEU_DB_PROJETOS_ID",
      "tarefas": "SEU_DB_TAREFAS_ID",
      "decisoes": "SEU_DB_DECISOES_ID"
    },
    "page_id": "SEU_PAGE_ID"
  },
  "qdrant": {
    "host": "localhost",
    "port": 6333,
    "collections": {
      "emails": "emails",
      "threads": "threads",
      "profiles": "profiles"
    }
  },
  "llm": {
    "provider": "openrouter",
    "model": "openrouter/z-ai/glm-5-turbo",
    "embedding_model": "text-embedding-3-small"
  },
  "gmail": {
    "accounts": {
      "seu@email.com": {
        "telegram_topic": null,
        "hook_token_env": "GOG_HOOK_TOKEN_PESSOAL"
      }
    }
  },
  "telegram": {
    "bot_token_env": "TELEGRAM_BOT_TOKEN",
    "chat_id": "SEU_CHAT_ID"
  }
}
```

> **Dica:** Para adicionar mais contas Gmail, adicione mais entradas em `gmail.accounts` com tokens diferentes.

### Passo 9: Subir os servicos

#### Opcao A: Docker Compose (recomendado para VPS)

```bash
docker-compose up -d
```

Isso inicia 3 servicos:
- **qdrant** — Vector database (porta 6333, limite 1GB RAM)
- **orchestrator** — FastAPI webhook server (porta 8787, limite 512MB RAM)
- **telegram-poller** — Bot de long-polling (limite 256MB RAM)

Verifique se esta tudo rodando:
```bash
docker-compose ps
docker-compose logs -f  # ver logs em tempo real
```

Teste o health check:
```bash
curl http://localhost:8787/health
```

#### Opcao B: Rodar manualmente (desenvolvimento)

```bash
# Instalar dependencias
pip install -r requirements.txt

# Terminal 1: Qdrant
docker run -p 6333:6333 -v ./qdrant/storage:/qdrant/storage qdrant/qdrant

# Terminal 2: Orchestrator
uvicorn orchestrator.main:app --host 127.0.0.1 --port 8787

# Terminal 3: Telegram Poller
python telegram_poller.py
```

### Passo 10: Configurar Tailscale Funnel

O Tailscale Funnel expoe uma porta local como HTTPS publico (necessario para o Pub/Sub do Google).

1. Instale o Tailscale e faca login:
   ```bash
   tailscale up
   ```
2. Habilite o Funnel apontando direto para o orchestrator:
   ```bash
   tailscale funnel --bg http://127.0.0.1:8787
   ```
3. Anote a URL gerada (formato: `https://sua-maquina.tail-xxxxx.ts.net`)

### Passo 11: Configurar Gmail Watch (Pub/Sub)

1. Crie a subscription do Pub/Sub apontando para o Tailscale Funnel:
   ```bash
   gcloud pubsub subscriptions create gmail-watch-sub \
     --topic=gmail-watch \
     --project=SEU_PROJETO \
     --push-endpoint="https://sua-maquina.tail-xxxxx.ts.net/hooks/gmail?token=SEU_TOKEN"
   ```
   Substitua `SEU_TOKEN` pelo valor de `GOG_HOOK_TOKEN_PESSOAL`.

2. Ative o Gmail Watch:
   ```bash
   python scripts/gmail_watch.py \
     --account seu@email.com \
     --topic projects/SEU_PROJETO/topics/gmail-watch
   ```

> **Importante:** O Gmail Watch expira a cada 7 dias. Configure um cron para renovar:
> ```bash
> # Adicionar ao crontab (crontab -e)
> 0 0 */6 * * cd /caminho/do/projeto && python scripts/gmail_watch.py --account seu@email.com --topic projects/SEU_PROJETO/topics/gmail-watch
> ```

### Passo 12: Testar

1. **Health check:**
   ```bash
   curl http://localhost:8787/health
   # Deve retornar: {"status":"healthy","services":{"notion":"connected",...}}
   ```

2. **Enviar um email de teste** para sua conta Gmail e verifique:
   - Os logs do orchestrator (`docker-compose logs -f orchestrator`)
   - A notificacao no Telegram
   - A decisao registrada no Notion

3. **Teste manual via API:**
   ```bash
   curl -X POST http://localhost:8787/hooks/gmail/test \
     -H "Content-Type: application/json" \
     -d '{"emailId":"SEU_EMAIL_ID","account":"seu@email.com"}'
   ```

---

## Configuracao Avancada (opcional)

### Company Profiles (Perfis Empresariais)

Para suporte a multiplas empresas, crie 3 databases adicionais no Notion:

**Company Profiles:**
- Nome (title), Conta Email (email), Setor (select), Tom (select), Assinatura (rich_text), Idioma Padrao (select)

**Clientes:**
- Nome (title), Contatos (rich_text - emails separados por virgula), Projeto Ativo (rich_text), Prioridade (select), Notas (rich_text), Company Profile (relation)

**Domain Rules:**
- Dominio (title - ex: `@pagar.me`), Categoria (select), Prioridade Minima (select), Acao Padrao (select), Company Profile (relation)

Adicione os IDs no `.env`:
```env
NOTION_DB_COMPANY_PROFILES=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
NOTION_DB_CLIENTES=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
NOTION_DB_DOMAIN_RULES=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

### Learning Engine

O motor de aprendizado roda automaticamente. Para ajustar:

```env
# Rodar ciclo de aprendizado a cada N emails (padrao: 50)
LEARNING_INTERVAL=50
```

### Migracao de feedback antigo

Se voce ja tem um `feedback.json` de versoes anteriores:

```bash
python scripts/migrate_feedback.py
```

### Variaveis de ambiente opcionais

| Variavel | Padrao | Descricao |
|----------|--------|-----------|
| `LLM_MODEL` | `z-ai/glm-5-turbo` | Modelo LLM no OpenRouter |
| `LEARNING_INTERVAL` | `50` | Emails entre cada ciclo de aprendizado |
| `EMAIL_AGENT_BASE_DIR` | auto-detectado | Diretorio base do projeto |
| `EMAIL_AGENT_LOG_FILE` | desabilitado | Habilitar log em arquivo |
| `TELEGRAM_WEBHOOK_SECRET` | nenhum | Secret para validar callbacks do Telegram |

---

## Pipeline de Processamento

```
1. Webhook recebido (Gmail Pub/Sub direto)
2. Deduplicacao (cache LRU in-memory, 1000 entradas)
3. Fetch email via Gmail API (async)
4. Parse e limpeza do corpo
5. Buscar contexto:
   a. Config da conta (Notion)
   b. Company profile + clientes + domain rules (Notion, cached 5 min)
   c. Emails similares (Qdrant, via embedding)
   d. Sender profile com padroes de correcao (Qdrant)
   e. Regras aprendidas (Qdrant)
6. Classificar com LLM (prompt enriquecido, max 6000 tokens)
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
│   │   ├── gmail_service.py              # Gmail API direta (OAuth)
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
├── credentials/                         # Tokens OAuth (nao commitado)
│   ├── client_secret.json               # Credencial OAuth do Google Cloud
│   └── token_seu@email.com.json         # Token por conta
├── scripts/
│   ├── gmail_auth.py                    # Setup OAuth para contas Gmail
│   └── migrate_feedback.py              # Migracao feedback.json → Qdrant
├── telegram_poller.py                   # Bot de callbacks
├── vip_manager.py                       # Gerencia VIP/blacklist
├── docker-compose.yml                   # 3 servicos (qdrant, orchestrator, poller)
├── Dockerfile                           # Python 3.11-slim
├── requirements.txt                     # Dependencias
├── .env.example                         # Template de variaveis
└── config.json                          # Config de accounts/databases
```

## Troubleshooting

### O bot do Telegram nao responde
- Verifique se o `telegram_poller.py` esta rodando (`docker-compose logs telegram-poller`)
- Confirme que o `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID` estao corretos no `.env`
- O bot precisa ser **admin do grupo** para ler mensagens

### Webhook nao recebe emails
- Verifique se o Tailscale Funnel esta ativo: `tailscale funnel status`
- Teste a URL do Funnel no navegador (deve retornar algo)
- Verifique se o Gmail Watch esta ativo (expira a cada 7 dias)
- Confira os logs do orchestrator

### Gmail retorna "not_ready" no health check
- Verifique se existe o diretorio `credentials/` com os tokens
- Execute `python scripts/gmail_auth.py --account seu@email.com` para re-autenticar
- Verifique se o `credentials/client_secret.json` existe

### Notion retorna "disconnected" no health check
- Verifique se `NOTION_API_KEY` e `NOTION_DB_CONFIG` estao no `.env`
- Confirme que a integracao tem acesso as databases (Conexoes)

### Qdrant retorna "disconnected"
- Verifique se o container esta rodando: `docker ps | grep qdrant`
- No Docker Compose, o host e `qdrant` (nao `localhost`)
- Localmente, o host e `localhost:6333`

### Erro "Rate limit excedido"
- O orchestrator permite 30 webhooks/minuto e 60 callbacks/minuto
- Se precisar mais, ajuste os `@limiter.limit()` em `orchestrator/main.py`
