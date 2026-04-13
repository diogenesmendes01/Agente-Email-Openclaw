# Email Agent Platform Redesign — Design Spec

## Visão Geral

Transformar o Email Agent de um agente pessoal single-account em uma plataforma multi-empresa com respostas automáticas configuráveis, persistência robusta e observabilidade operacional.

## Decisões Arquiteturais

| Decisão | Escolha | Motivo |
|---------|---------|--------|
| Banco de dados | PostgreSQL 16 | Substitui Notion + JSONs. Suporta concorrência, queries, métricas |
| Notion | Eliminado | Latência alta, rate limit 3 req/s, redundante com Postgres |
| config.json | Eliminado | `.env` para secrets/infra, Postgres para config por conta |
| Telegram | Webhook (elimina polling) | -1 container, latência zero, conexão única com banco |
| PDF | pdfplumber + Gemini 2.5 Flash (fallback vision) | Texto local quando possível, OCR via LLM quando necessário |
| Modelo vision | Gemini 2.5 Flash via OpenRouter | Qualidade alta, preço baixo, usado só quando pdfplumber falha |
| Resiliência | Retry tenacity + fila Postgres | Sem circuit breaker por agora |
| Observabilidade | Request ID + métricas Postgres + alertas DM | Sem dashboard externo por agora |
| Playbooks | YAML local (não commitado) + import Postgres + config via Telegram | Editável, versionável localmente, dados sensíveis fora do Git |
| Alertas sistema | DM privada bot → operador | Separado do grupo de empresas |

---

## Fase 1 — Fundação (PostgreSQL + Config + PDF)

Pré-requisito de todas as fases seguintes.

### 1.1 PostgreSQL no Docker Compose

Novo container adicionado ao `docker-compose.yml`:

```yaml
postgres:
  image: postgres:16-alpine
  container_name: email-agent-postgres
  environment:
    POSTGRES_DB: emailagent
    POSTGRES_USER: emailagent
    POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
  volumes:
    - ./pgdata:/var/lib/postgresql/data
  ports:
    - "127.0.0.1:5432:5432"
  restart: unless-stopped
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U emailagent"]
    interval: 10s
    timeout: 5s
    retries: 5
```

Nova env var:
```env
DATABASE_URL=postgresql://emailagent:senha@postgres:5432/emailagent
POSTGRES_PASSWORD=senha_segura_aqui
```

O orchestrator ganha `depends_on: postgres: condition: service_healthy`.

### 1.2 Schema do Banco

```sql
-- Contas Gmail registradas
CREATE TABLE accounts (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    hook_token_env VARCHAR(100) NOT NULL,
    oauth_token_path VARCHAR(255),      -- ex: "credentials/token_user@gmail.com.json"
    telegram_topic_id BIGINT,
    learning_counter INT DEFAULT 0,     -- migra de Qdrant
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- VIPs por conta (substitui vip-list.json)
CREATE TABLE vip_list (
    id SERIAL PRIMARY KEY,
    account_id INT REFERENCES accounts(id) ON DELETE CASCADE,
    sender_email VARCHAR(255) NOT NULL,
    sender_name VARCHAR(255),
    min_urgency VARCHAR(20) DEFAULT 'high',
    added_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(account_id, sender_email)
);

-- Blacklist por conta (substitui blacklist.json)
CREATE TABLE blacklist (
    id SERIAL PRIMARY KEY,
    account_id INT REFERENCES accounts(id) ON DELETE CASCADE,
    sender_email VARCHAR(255) NOT NULL,
    reason VARCHAR(255),
    added_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(account_id, sender_email)
);

-- Feedback de reclassificação (substitui feedback.json)
CREATE TABLE feedback (
    id SERIAL PRIMARY KEY,
    account_id INT REFERENCES accounts(id) ON DELETE CASCADE,
    email_id VARCHAR(100) NOT NULL,
    sender VARCHAR(255),
    original_urgency VARCHAR(20),
    corrected_urgency VARCHAR(20),
    keywords TEXT[],
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Log de decisões (substitui Notion DB Decisões)
CREATE TABLE decisions (
    id SERIAL PRIMARY KEY,
    account_id INT REFERENCES accounts(id) ON DELETE CASCADE,
    email_id VARCHAR(100) NOT NULL,
    subject TEXT,
    sender VARCHAR(255),
    classification VARCHAR(50),
    priority VARCHAR(20),
    category VARCHAR(50),
    action VARCHAR(50),
    summary TEXT,
    reasoning_tokens INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Tarefas (substitui Notion DB Tarefas)
CREATE TABLE tasks (
    id SERIAL PRIMARY KEY,
    account_id INT REFERENCES accounts(id) ON DELETE CASCADE,
    email_id VARCHAR(100),
    title TEXT NOT NULL,
    priority VARCHAR(20) DEFAULT 'Média',
    status VARCHAR(20) DEFAULT 'Pendente',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- History IDs do Gmail Watch (substitui history_ids.json)
CREATE TABLE history_ids (
    account_id INT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    history_id VARCHAR(50) NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 1.3 Objeto de Settings Unificado

Novo arquivo: `orchestrator/settings.py`

Carrega e valida todas as configurações do `.env` na inicialização. Falha rápido com mensagem clara se faltar algo obrigatório.

```python
class Settings:
    # LLM
    openrouter_api_key: str          # obrigatório
    openai_api_key: str              # obrigatório
    llm_model: str                   # default: "z-ai/glm-5-turbo"
    llm_vision_model: str            # default: "google/gemini-2.5-flash"
    embedding_model: str             # default: "text-embedding-3-small"

    # Telegram
    telegram_bot_token: str          # obrigatório
    telegram_chat_id: str            # obrigatório
    telegram_allowed_user_ids: set[int]  # obrigatório
    telegram_webhook_secret: str     # obrigatório

    # Database
    database_url: str                # obrigatório

    # Qdrant
    qdrant_host: str                 # default: "localhost"
    qdrant_port: int                 # default: 6333

    # Gmail
    gmail_accounts: dict[str, str]   # mapa email → hook_token_env (do .env)

    # Tailscale Funnel
    funnel_base_url: str             # obrigatório — ex: "https://maquina.ts.net"

    # Alertas
    telegram_alert_user_id: int      # obrigatório — seu user ID para DMs
```

Singleton acessível em todo o código: `from orchestrator.settings import settings`

**Ciclo de vida da conexão com Postgres:**

```python
# Em main.py, usando FastAPI lifespan:
from contextlib import asynccontextmanager
import asyncpg

@asynccontextmanager
async def lifespan(app):
    # Startup: criar pool de conexões
    app.state.db_pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=2,
        max_size=10
    )
    yield
    # Shutdown: fechar pool
    await app.state.db_pool.close()

app = FastAPI(lifespan=lifespan)
```

O `DatabaseService` recebe o pool via injeção no construtor, não cria conexões próprias.

### 1.4 Leitura de PDF

Novo arquivo: `orchestrator/utils/pdf_reader.py`

Fluxo de extração:

1. `gmail_service.py` ganha método `get_attachment(email_id, attachment_id, account) -> bytes`
2. `pdf_reader.py` recebe os bytes e:
   - Tenta `pdfplumber` para extração de texto
   - Se sucesso: retorna texto (≤10 páginas completo, >10 páginas = 5 primeiras + 2 últimas)
   - Se falha (PDF escaneado/imagem): converte páginas em imagem e envia para Gemini 2.5 Flash via OpenRouter (mesmos limites de páginas)
3. Texto extraído é anexado ao `body_clean` do email antes da classificação
4. Nova env var: `LLM_VISION_MODEL=google/gemini-2.5-flash`
   - Fallback se modelo indisponível no OpenRouter: `openai/gpt-4o-mini` (também suporta vision)
   - Verificar disponibilidade na inicialização do Settings

Integração no `email_processor.py` — após parse do email, antes da classificação:

```
# Após step 2 (parse)
# Novo step 2.5: extrair texto de anexos PDF
for attachment in email["attachments"]:
    if attachment["mimeType"] == "application/pdf":
        pdf_bytes = await gmail.get_attachment(email_id, attachment["id"], account)
        pdf_text = await pdf_reader.extract(pdf_bytes)
        email["body_clean"] += f"\n\n--- ANEXO PDF: {attachment['filename']} ---\n{pdf_text}"
```

### 1.5 Camada de Acesso ao Banco

Novo arquivo: `orchestrator/services/database_service.py`

Usa `asyncpg` para queries async. Métodos que substituem o NotionService e vip_manager:

```python
class DatabaseService:
    async def get_account(email: str) -> dict
    async def get_account_config(email: str) -> dict

    # VIP
    async def add_vip(account_id, sender_email, sender_name, min_urgency)
    async def remove_vip(account_id, sender_email)
    async def is_vip(account_id, sender_email) -> bool
    async def get_vips(account_id) -> list

    # Blacklist
    async def add_to_blacklist(account_id, sender_email, reason)
    async def remove_from_blacklist(account_id, sender_email)
    async def is_blacklisted(account_id, sender_email) -> bool

    # Feedback
    async def save_feedback(account_id, email_id, sender, original, corrected, keywords)

    # Decisões
    async def log_decision(decision: dict) -> int
    # Mapeamento de chaves do email_processor para colunas:
    # "from" → sender, "classificacao" → classification,
    # "prioridade" → priority, "categoria" → category,
    # "acao" → action, "resumo" → summary

    # Tarefas
    async def create_task(account_id, title, priority, email_id) -> int

    # History IDs
    async def get_history_id(account_id) -> str
    async def save_history_id(account_id, history_id)
```

### 1.6 Eliminações

| Remove | Motivo |
|--------|--------|
| `orchestrator/services/notion_service.py` | Postgres substitui |
| `orchestrator/services/company_service.py` | Postgres substitui (clients/domain_rules migram para Fase 4) |
| `orchestrator/services/gog_service.py` | Já substituído pelo gmail_service.py, arquivo residual |
| `config.json` | `.env` + Postgres substitui |
| `vip-list.json`, `blacklist.json`, `feedback.json` | Postgres substitui |
| `history_ids.json` | Postgres substitui |
| `vip_manager.py` | Lógica migra para `database_service.py` |
| Dependência `notion-client` em `requirements.txt` | Não precisa mais |
| `PyMuPDF` em `requirements.txt` | `pdfplumber` substitui (mais leve, pure Python) |

**Nota:** `pending_actions.json` e `pending_replies.json` são eliminados na **Fase 3**, não nesta fase.

### 1.6b Qdrant — O Que Muda e O Que Fica

O Qdrant **permanece** para embeddings e busca vetorial. Não é substituído pelo Postgres.

| Dado | Onde fica | Motivo |
|------|----------|--------|
| Email embeddings | Qdrant | Busca por similaridade vetorial |
| Sender profiles | Qdrant | Padrões de comportamento do remetente |
| Learned rules (LearningEngine) | Qdrant | Regras aprendidas por embedding |
| Learning counter | Postgres (nova coluna em `accounts`) | Era stored no Qdrant, mais natural no Postgres |
| Feedback (reclassificação) | Postgres (tabela `feedback`) | Substituiu feedback.json. Qdrant recebe cópia para enriquecer sender profile |
| VIP, blacklist | Postgres | Não precisa de busca vetorial |

A `LearningEngine` continua funcionando como está — analisa feedback do Postgres, gera regras, armazena no Qdrant. A única mudança é que lê feedback do Postgres em vez do JSON.

### 1.6c Atualização do Health Check

O endpoint `/health` deve verificar Postgres em vez de Notion:

```python
"postgres": "connected" if db.is_connected() else "disconnected",
# Remove: "notion": "connected" if notion.is_connected() else "disconnected"
```

### 1.7 Script de Migração

Novo arquivo: `scripts/migrate_to_postgres.py`

1. Lê JSONs existentes (vip-list.json, blacklist.json, feedback.json)
2. Cria account no Postgres baseado no `.env`
3. Insere dados migrando o campo `account` para `account_id`
4. Confirma contagens e integridade

**Deploy:** parar os containers antes de migrar. `pending_actions.json` e `pending_replies.json` contêm estado efêmero que expira naturalmente — não precisa migrar, basta aguardar ações pendentes expirarem (~10 min) antes do deploy.

### 1.8 Novas Dependências

```
asyncpg          # Driver PostgreSQL async
pdfplumber       # Extração de texto de PDF
Pillow           # Conversão de página PDF para imagem (para vision)
```

---

## Fase 2 — Observabilidade + Resiliência

### 2.1 Request ID

Middleware FastAPI que gera UUID para cada request e injeta em todos os logs:

```python
@app.middleware("http")
async def request_id_middleware(request, call_next):
    request_id = str(uuid.uuid4())[:8]
    # Injetar no contexto de logging
    ...
```

Formato de log muda para: `[req:a1b2c3d4] [email_id] mensagem`

Permite rastrear: webhook recebido → email processado → notificação enviada.

### 2.2 Métricas no Postgres

Nova tabela:

```sql
CREATE TABLE metrics (
    id SERIAL PRIMARY KEY,
    request_id VARCHAR(8),
    account_id INT REFERENCES accounts(id),
    event VARCHAR(50) NOT NULL,        -- 'email_processed', 'email_failed', 'llm_call', etc.
    service VARCHAR(30),               -- 'gmail', 'llm', 'qdrant', 'telegram'
    latency_ms INT,
    tokens_used INT,
    success BOOLEAN DEFAULT true,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_metrics_created ON metrics(created_at);
CREATE INDEX idx_metrics_event ON metrics(event);

-- Retenção: job diário deleta registros com mais de 90 dias
-- DELETE FROM metrics WHERE created_at < NOW() - INTERVAL '90 days';
```

Cada serviço registra métricas automaticamente via decorator ou context manager.

### 2.3 Alertas via DM

Novo módulo: `orchestrator/services/alert_service.py`

Envia mensagem privada para o operador quando:
- Token OAuth expirado e não renovou
- Serviço externo falhou 3+ vezes consecutivas
- Fila de retry com jobs acumulados (>10)
- Gmail Watch expirando em <24h

Usa `TELEGRAM_ALERT_USER_ID` (seu user ID) para enviar DM via bot.

Throttling: máximo 1 alerta do mesmo tipo a cada 15 minutos (evita flood).

### 2.4 Retry com Tenacity

Aplicar o mesmo padrão que o LLM já usa em todos os serviços externos:

```python
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((TimeoutError, ConnectionError)),
    reraise=True
)
```

Serviços afetados:
- `gmail_service.py` — get_email, send_reply, archive, get_history
- `telegram_service.py` — send_notification, answer_callback
- `qdrant_service.py` — search_similar, store_email

### 2.5 Fila de Jobs Falhados

Nova tabela:

```sql
CREATE TABLE failed_jobs (
    id SERIAL PRIMARY KEY,
    account_id INT REFERENCES accounts(id),
    job_type VARCHAR(50) NOT NULL,     -- 'process_email', 'send_notification', etc.
    payload JSONB NOT NULL,
    attempts INT DEFAULT 0,
    max_attempts INT DEFAULT 5,
    last_error TEXT,
    next_retry_at TIMESTAMPTZ,
    status VARCHAR(20) DEFAULT 'pending',  -- 'pending', 'processing', 'completed', 'dead'
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

Worker async no FastAPI que processa jobs pendentes a cada 60 segundos com backoff exponencial. Jobs que falham `max_attempts` vezes viram `dead` e geram alerta DM.

---

## Fase 3 — Telegram Webhook + Separação de Responsabilidades

### 3.1 Migração para Webhook

O endpoint `POST /telegram/callback` já existe. Mudanças:

1. Registrar webhook no Telegram na inicialização (usa `settings.funnel_base_url`):
   ```python
   # Dentro do lifespan do FastAPI
   url = f"{settings.funnel_base_url}/telegram/callback"
   await telegram.set_webhook(url, secret_token=settings.telegram_webhook_secret)
   ```

2. Remover container `telegram-poller` do `docker-compose.yml`
3. Deletar `telegram_poller.py`

### 3.2 Nova Estrutura de Módulos

```
orchestrator/
  handlers/
    email_processor.py          # Pipeline de email (já existe)
    telegram_callbacks.py       # Routing de callbacks → ações
    telegram_commands.py        # Comandos /config_*, /help, etc.
  actions/
    __init__.py
    archive.py                  # Arquivar email no Gmail
    vip.py                      # Adicionar/remover VIP
    silence.py                  # Adicionar/remover blacklist
    feedback.py                 # Reclassificação + salvar feedback
    reply.py                    # Resposta customizada via LLM + Gmail
    task.py                     # Criar tarefa no Postgres
    spam.py                     # Marcar como spam no Gmail + blacklist
  services/
    telegram_service.py         # Envio de mensagens (já existe, expandido)
    gmail_service.py            # Gmail API (já existe)
    llm_service.py              # LLM (já existe)
    database_service.py         # Postgres (Fase 1)
    qdrant_service.py           # Vector DB (já existe)
    alert_service.py            # Alertas DM (Fase 2)
```

### 3.3 telegram_callbacks.py

Recebe o callback do webhook, valida auth, e despacha:

```python
async def handle_callback(callback_query: dict, actor_id: int, chat_id: int):
    action_type, email_id, account = parse_callback_data(callback_query["data"])

    # Validação (já existe em security.py)
    # Actor ownership check
    # Despachar para action correta

    action_map = {
        "archive": actions.archive.execute,
        "vip": actions.vip.execute,
        "silence": actions.silence.execute,
        "spam": actions.spam.execute,
        "reclassify": actions.feedback.start_reclassify,
        "custom_reply": actions.reply.start_reply,
        "create_task": actions.task.execute,
        "send_draft": actions.reply.send_draft,
    }
```

### 3.4 Pending State no Postgres

Nova tabela (substitui pending_actions.json e pending_replies.json):

```sql
CREATE TABLE pending_actions (
    id SERIAL PRIMARY KEY,
    account_id INT REFERENCES accounts(id),
    email_id VARCHAR(100) NOT NULL,
    action_type VARCHAR(50) NOT NULL,
    actor_id BIGINT NOT NULL,
    state JSONB DEFAULT '{}',
    expires_at TIMESTAMPTZ DEFAULT NOW() + INTERVAL '10 minutes',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_pending_email ON pending_actions(email_id);
```

TTL automático — limpar ações expiradas a cada minuto.

### 3.5 Eliminações

| Remove | Motivo |
|--------|--------|
| `telegram_poller.py` | Lógica migrada para handlers/ e actions/ |
| Container `telegram-poller` no docker-compose.yml | Webhook no FastAPI substitui |
| `pending_actions.json` | Postgres substitui |
| `pending_replies.json` | Postgres substitui |

### 3.6 Atualizações Necessárias

- **`ALLOWED_CALLBACK_ACTIONS`** em `main.py`: expandir para incluir `vip`, `silence`, `spam`, `reclassify`, `custom_reply` (antes só existiam no poller)
- **`docker-compose.yml`**: remover seção `telegram-poller`, remover volumes de `config.json`, `pending_actions.json`, `pending_replies.json`
- **`Dockerfile`**: remover cópia de `telegram_poller.py`, `vip_manager.py` e criação de arquivos JSON de estado
- **Pending state para reply flow**: o `state JSONB` na tabela `pending_actions` armazena `{"draft_text": "...", "to": "...", "subject": "...", "thread_id": "..."}` para o fluxo de send_draft

---

## Fase 4 — Playbooks Multi-Empresa

### 4.1 Schema de Playbooks no Postgres

```sql
-- Identidade da empresa (vinculada à conta Gmail)
CREATE TABLE company_profiles (
    id SERIAL PRIMARY KEY,
    account_id INT UNIQUE REFERENCES accounts(id) ON DELETE CASCADE,
    company_name VARCHAR(255) NOT NULL,
    cnpj VARCHAR(20),
    tone TEXT,                          -- "formal, empático, objetivo"
    signature TEXT,                     -- assinatura completa
    whatsapp_url VARCHAR(500),
    extra_config JSONB DEFAULT '{}',    -- extensível
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Clientes da empresa (migra de CompanyService/Notion)
CREATE TABLE clients (
    id SERIAL PRIMARY KEY,
    company_id INT REFERENCES company_profiles(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    contacts TEXT,                       -- emails separados por vírgula
    active_project VARCHAR(255),
    priority VARCHAR(20) DEFAULT 'Média',
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Regras de domínio (migra de CompanyService/Notion)
CREATE TABLE domain_rules (
    id SERIAL PRIMARY KEY,
    company_id INT REFERENCES company_profiles(id) ON DELETE CASCADE,
    domain VARCHAR(255) NOT NULL,        -- ex: "@pagar.me"
    category VARCHAR(50),
    min_priority VARCHAR(20),
    default_action VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(company_id, domain)
);

-- Playbooks de resposta automática
CREATE TABLE playbooks (
    id SERIAL PRIMARY KEY,
    company_id INT REFERENCES company_profiles(id) ON DELETE CASCADE,
    trigger_description TEXT NOT NULL,   -- "dúvida sobre boleto/proposta"
    auto_respond BOOLEAN DEFAULT true,
    response_template TEXT NOT NULL,     -- template com {nome_contato}, {razao_social}, etc.
    priority INT DEFAULT 0,             -- ordem de matching (maior = primeiro)
    active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 4.2 Fluxo de Processamento com Playbooks

```
Email chega para contato@codewave.com
    │
    ├── 1. Fetch + parse corpo + anexos PDF
    │
    ├── 2. Identificar empresa remetente (CNPJ + Razão Social)
    │      └── LLM extrai do corpo/anexos
    │
    ├── 3. Carregar playbooks da CodeWave do Postgres
    │
    ├── 4. LLM classifica intenção e faz match com playbook
    │      Matching: LLM recebe o email + lista de playbooks e retorna
    │      qual playbook se aplica (ou nenhum). Uma única chamada LLM,
    │      não uma por playbook. Custo = 1 chamada extra por email de empresa.
    │      ├── Match encontrado + auto_respond=true
    │      │   └── Gerar resposta baseada no template + tom da empresa
    │      └── Sem match
    │          └── Classificação normal (pipeline existente)
    │
    ├── 5. Se auto_respond: enviar resposta via Gmail
    │
    └── 6. Notificar no tópico da CodeWave no Telegram:
           ├── CNPJ: 12.345.678/0001-90
           ├── Razão Social: Empresa X Ltda
           ├── Intenção: "cancelamento de envio"
           ├── Ação: "Resposta automática enviada"
           └── [✅ OK] [✏️ Editar resposta] [🔇 Silenciar]
```

### 4.3 Configuração via Telegram

Comandos disponíveis no tópico de cada empresa:

```
/config_identidade
  → Bot pergunta: nome, CNPJ, tom, assinatura, WhatsApp
  → Salva em company_profiles

/config_playbook
  → Bot pergunta: gatilho, template de resposta, auto ou manual?
  → Salva em playbooks

/config_playbook_list
  → Lista todos os playbooks ativos

/config_playbook_delete <id>
  → Remove um playbook
```

Fluxo de configuração é conversacional — bot pergunta uma coisa por vez, salva no Postgres.

### 4.4 Sugestões da LLM

Após processar N emails de uma empresa, a LLM analisa padrões e sugere novos playbooks:

```
🤖 Sugestão para CodeWave:

Notei que 80% das reclamações mencionam "segunda via".
Sugiro adicionar um playbook automático:

Gatilho: "segunda via de boleto"
Resposta: "Prezado(a), segue o link para segunda via..."

[✅ Aprovar] [❌ Rejeitar] [✏️ Editar]
```

Sugestão vai para o tópico da empresa. Só aplica se aprovada.

### 4.5 YAML de Referência

Arquivo `playbooks/modelo.yaml.example` commitado no repo com dados fictícios:

```yaml
empresa: "Nome da Empresa"
cnpj: "00.000.000/0001-00"
tom: "formal, empático"
assinatura: |
  Atenciosamente,
  Equipe {empresa}
whatsapp_reembolso: "https://wa.me/5500000000000"

playbooks:
  - gatilho: "dúvida sobre boleto/proposta"
    auto: true
    template: |
      Prezado(a) {nome_contato},
      ...
```

Pasta `playbooks/` no `.gitignore`. Script `scripts/import_playbooks.py` importa YAMLs para o Postgres (opcional, para quem preferir editar localmente).

---

## Estrutura Final do Projeto

```
Agente-Email-Openclaw/
├── orchestrator/
│   ├── main.py                          # FastAPI app + webhooks
│   ├── settings.py                      # Settings unificado do .env
│   ├── security.py                      # Auth helpers
│   ├── handlers/
│   │   ├── email_processor.py           # Pipeline de email
│   │   ├── telegram_callbacks.py        # Routing callbacks → ações
│   │   └── telegram_commands.py         # /config_*, /help
│   ├── actions/
│   │   ├── archive.py
│   │   ├── vip.py
│   │   ├── silence.py
│   │   ├── feedback.py
│   │   ├── reply.py
│   │   ├── task.py
│   │   └── spam.py
│   ├── services/
│   │   ├── database_service.py          # PostgreSQL
│   │   ├── gmail_service.py
│   │   ├── llm_service.py
│   │   ├── qdrant_service.py
│   │   ├── telegram_service.py
│   │   └── alert_service.py
│   └── utils/
│       ├── email_parser.py
│       ├── text_cleaner.py
│       └── pdf_reader.py
├── scripts/
│   ├── gmail_auth.py
│   ├── gmail_watch.py
│   ├── migrate_to_postgres.py
│   └── import_playbooks.py
├── playbooks/                           # .gitignore (dados sensíveis)
│   └── modelo.yaml.example             # referência commitada
├── sql/
│   └── schema.sql                       # Schema completo
├── tests/
├── docker-compose.yml                   # postgres + qdrant + orchestrator (3 containers)
├── Dockerfile
├── requirements.txt
└── .env.example
```

## Docker Compose Final

3 containers (antes eram 4):

| Container | Serviço | RAM |
|-----------|---------|-----|
| `postgres` | PostgreSQL 16 | ~100MB |
| `qdrant` | Vector DB | ~1GB |
| `orchestrator` | FastAPI (webhooks Gmail + Telegram + API) | ~512MB |

Eliminado: `telegram-poller`

## Ordem de Implementação

| Fase | Entrega | Depende de |
|------|---------|------------|
| 1 | Postgres + settings + PDF + eliminar Notion/JSONs | — |
| 2 | Request ID + métricas + alertas DM + retry + fila | Fase 1 |
| 3 | Webhook Telegram + módulos + eliminar poller | Fase 1 |
| 4 | Playbooks multi-empresa + config via Telegram | Fase 1 + 3 |

Fases 2 e 3 podem ser executadas em paralelo após Fase 1.
