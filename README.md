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

### Deteccao de emails nao-respondiveis

Sistema reconhece emails que nao precisam/nao podem receber resposta:
- **Senders no-reply** (regex em `noreply@`, `mailer-daemon@`, `notifications@`, etc.)
- **Categorias automatizadas** (`newsletter`, `promocao`, `notificacao_automatica`, `transacional`)

Para esses emails:
- O LLM **nao gera rascunho de resposta** (economia de tokens)
- A coluna `decisions.no_reply_detected` registra o flag para auditoria
- Flag opcional `NO_REPLY_AUTO_ARCHIVE=true` (default `false`) faz com que esses emails sejam arquivados automaticamente sem chamar o LLM de acao

Rascunhos gerados (quando aplicaveis) ficam apenas no Telegram — nao sao mais salvos na pasta "Rascunhos" do Gmail. Use o botao "Enviar" no Telegram para mandar a resposta direto.

### Prompts em 3 Camadas (Sistema / Tarefa / Usuario)
Todo prompt enviado ao LLM e construido em 3 camadas compostas, com protecao anti-injection:

- **Camada 1 — Regras de sistema (IMUTAVEIS):** 8 regras invioláveis sempre no topo ("nunca inventar dados", "nunca omitir valores/datas do email", "se um PDF nao foi lido, nao fingir que leu", etc.)
- **Camada 2 — Instrucoes da tarefa:** config estruturada por kind (`classification`, `summary`, `action`) controlada em codigo — categorias validas, tamanho de resumo, campos obrigatorios no rascunho
- **Camada 3 — Personalizacao por conta:** config estruturada editavel via Telegram (tom adicional, instrucoes extras, categorias extras, tamanho do rascunho, instrucoes livres de ate 500 chars)

Todos os campos editaveis passam por `validate_layer3_field` (rejeita padroes tipo `ignore`, `override`, `desconsidere`). Defesa em profundidade: filtro tanto no save (Telegram handler rejeita) quanto no render (valores invalidos persistidos por outra via sao omitidos com log). Camada 3 nunca precede Camada 1 no prompt final.

### Validacao Pos-LLM (Retry + Quality Log)
Nunca confia cego na saida do LLM:

- **Schemas Pydantic** para classifier/summarizer/action (clamp de campos invalidos: categoria fora da lista -> "outro", confianca > 1 tratada como porcentagem, etc.)
- **Validacao semantica** com flags: `rascunho_sem_valor` (email menciona R$ mas rascunho nao), `rascunho_sem_data`, `rascunho_muito_curto`, `resumo_generico` (regex de chavoes), `rascunho_inventado` (rascunho cita valor/data que NAO existe no email)
- **Pipeline `validate_and_retry`:** 1 retry reforcado no parse-fail, schema-fail ou flag bloqueante. Em caso de `rascunho_inventado` persistente apos retry, o rascunho e REMOVIDO e substituido por nota explicativa
- **Tabela `llm_quality_log`**: telemetria por email (flags disparadas, retries, tokens por tentativa vs tokens da tentativa aceita, fallback_used) — permite sintonia baseada em dados apos periodo de coleta
- **API:** `classify_email`, `summarize_email` e `decide_action` retornam tupla `(result, ValidationMetadata)` com escopo por email (zero vazamento de metadados entre emails concorrentes)

### PDFs Robustos (Digital / Escaneado / Protegido / Corrompido)
Regra do sistema: **"se nao leu, nao finge que leu".** Detecta tipo do PDF e age conforme:

- **Digital**: extrai texto via `pdfplumber` + regex para valores (R$), datas, CPF/CNPJ (mascarados), protocolos — campos destacados no prompt
- **Escaneado**: fallback OCR via modelo de visao (Gemini)
- **Protegido por senha** (3 cenarios):
  1. Senha cadastrada via `/pdf_senha <pattern> <senha>` (Fernet-encrypted na tabela `pdf_passwords`, escopada por pattern tipo `*@bradesco.com.br`)
  2. **Inferencia do corpo do email** (opt-in): se o email menciona "CPF"/"CNPJ"/"nascimento", tenta combinacoes derivadas dos docs cadastrados em `/config_documentos` (tambem Fernet-encrypted)
  3. **Desconhecida**: anexo marcado como NAO LIDO; body enviado ao LLM recebe bloco explicito `--- ANEXO PDF NAO LIDO ---` e a Camada 1 proibe o LLM de adivinhar
- **Rate-limit escopado ao pattern**: sucesso so limpa contador do pattern efetivamente usado; falha so bloqueia patterns que foram de fato tentados contra aquele PDF
- **Corrompido**: anexo ignorado com motivo explicito; nunca passa texto invalido ao LLM

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

**Identidade da empresa e playbooks:**
- `/config_identidade` - Configura nome, CNPJ, tom e assinatura da empresa (conversacional)
- `/config_playbook` - Cria novo playbook com gatilho, template e modo (auto/manual)
- `/config_playbook_list` - Lista playbooks ativos
- `/config_playbook_delete <id>` - Remove playbook
- `/config_modelo` - Seleciona modelo LLM especifico para a conta (override do default)

**Prompts (sistema de 3 camadas):**
- `/config_prompt` - Wizard conversacional para editar Camada 3 (tom adicional, instrucoes extras, categorias extras, tamanho do rascunho, instrucoes livres)
- `/prompt_ver` - Mostra config atual da conta + preview do prompt final montado para um email de exemplo (nao chama o LLM)
- `/prompt_regras` - Exibe as 8 regras imutaveis da Camada 1
- `/prompt_reset` - Volta a config da conta ao default (apaga Camada 3)

**PDFs protegidos por senha:**
- `/pdf_senha <pattern> <senha>` - Cadastra senha para um pattern de remetente (ex: `*@bradesco.com.br`). Senha armazenada com Fernet encryption.
- `/pdf_senhas` - Lista senhas cadastradas (senhas sempre mascaradas como `****`)
- `/pdf_senha_remove <pattern>` - Remove todas as senhas para o pattern
- `/config_documentos` - Cadastra CPF/CNPJ/data de nascimento criptografados (opt-in), usados apenas para tentar inferir senhas de PDFs quando o corpo do email menciona esses documentos

**Utilitarios:**
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

Este guia cobre **tudo** que voce precisa fazer antes e durante a configuracao, incluindo a criacao de contas e obtencao de IDs em servicos externos.

---

### Fase 1: Preparar o Servidor

#### 1.1 Pacotes do sistema (Ubuntu/Debian)

```bash
apt update && apt install -y python3 python3-pip python3-venv python-is-python3 \
  git curl postgresql docker.io docker-compose
```

> O wizard (`setup_wizard.py`) instala as dependencias Python automaticamente. Voce **nao** precisa rodar `pip install` manualmente.

#### 1.2 Instalar e configurar PostgreSQL

```bash
# Iniciar o servico
systemctl start postgresql
systemctl enable postgresql

# Criar usuario e banco
sudo -u postgres psql <<EOF
CREATE USER emailagent WITH PASSWORD 'SUA_SENHA_AQUI';
CREATE DATABASE emailagent OWNER emailagent;
GRANT ALL PRIVILEGES ON DATABASE emailagent TO emailagent;
\c emailagent
GRANT ALL ON SCHEMA public TO emailagent;
EOF
```

Teste a conexao:
```bash
psql "postgresql://emailagent:SUA_SENHA_AQUI@localhost:5432/emailagent" -c "SELECT 1;"
```

#### 1.3 Instalar Qdrant (Vector Database)

```bash
docker run -d --name qdrant --restart always \
  -p 6333:6333 \
  -v /root/qdrant_data:/qdrant/storage \
  qdrant/qdrant:latest
```

Verifique: `curl http://localhost:6333/collections` deve retornar `{"result":{"collections":[]},"status":"ok"}`.

#### 1.4 Instalar e configurar Tailscale Funnel

O Tailscale Funnel expoe sua VPS via HTTPS (necessario para receber webhooks do Google Pub/Sub e Telegram).

```bash
# Instalar Tailscale
curl -fsSL https://tailscale.com/install.sh | sh

# Conectar (abre link para login)
tailscale up

# Ativar Funnel apontando para a porta do agente
tailscale funnel --bg 8787
```

Anote a URL gerada (formato: `https://seu-hostname.tail-xxxxx.ts.net`). Voce vai usar ela nos passos seguintes.

> **Dica:** Para ver a URL: `tailscale funnel status`

---

### Fase 2: Criar o Bot do Telegram

#### 2.1 Criar o bot no BotFather

1. No Telegram, abra conversa com **@BotFather**
2. Envie `/newbot`
3. Escolha um **nome** (ex: "Email Agent") e um **username** (ex: `email_agent_xyz_bot`)
4. O BotFather retorna um **token** — guarde-o:
   ```
   123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```

#### 2.2 Desativar Group Privacy

**Importante:** Por padrao, bots so recebem mensagens que sao comandos (`/start`) ou que mencionam o bot. Para o agente funcionar em grupos, desative o Group Privacy:

1. No @BotFather, envie `/mybots`
2. Selecione seu bot
3. **Bot Settings** > **Group Privacy** > **Turn off**
4. Deve aparecer: *"Privacy mode is disabled"*

#### 2.3 Criar grupo e obter Chat ID

1. Crie um **grupo** no Telegram (ou use um existente)
2. Se quiser usar **Topics** (forum), ative em: Configuracoes do grupo > Topics
3. **Adicione o bot ao grupo** e promova a **admin**
4. Envie uma mensagem qualquer no grupo
5. Abra no navegador:
   ```
   https://api.telegram.org/bot<SEU_TOKEN>/getUpdates
   ```
6. Procure pelo campo `"chat":{"id":-100xxxxxxxxxx}` — esse numero negativo e o **Chat ID**

> **Se getUpdates retornar vazio:** Verifique se nao ha outra instancia do bot rodando (getUpdates so funciona com uma instancia). Delete o webhook primeiro: `https://api.telegram.org/bot<TOKEN>/deleteWebhook`

#### 2.4 Obter seu User ID (para alertas DM)

O agente envia alertas de erro diretamente na sua DM. Para isso, precisa do seu **User ID**:

1. No Telegram, abra conversa com **@userinfobot**
2. Envie qualquer mensagem
3. Ele responde com seu **User ID** (numero positivo, ex: `947563152`)

---

### Fase 3: Configurar Google Cloud (Gmail API + Pub/Sub)

#### 3.1 Criar projeto no Google Cloud

1. Acesse [console.cloud.google.com](https://console.cloud.google.com/)
2. Clique em **Selecionar projeto** > **Novo projeto**
3. Escolha um nome (ex: `email-agent`) e clique em **Criar**
4. Anote o **Project ID** (ex: `email-agent-493213`) — voce vai usar nos comandos abaixo

#### 3.2 Instalar Google Cloud CLI (gcloud)

Se ainda nao tem o `gcloud` instalado:

```bash
# Ubuntu/Debian
curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg
echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | sudo tee /etc/apt/sources.list.d/google-cloud-sdk.list
apt update && apt install -y google-cloud-cli

# Autenticar
gcloud auth login
gcloud config set project SEU_PROJECT_ID
```

#### 3.3 Ativar APIs necessarias

```bash
gcloud services enable gmail.googleapis.com --project=SEU_PROJECT_ID
gcloud services enable pubsub.googleapis.com --project=SEU_PROJECT_ID
```

#### 3.4 Configurar tela de consentimento OAuth

1. No Google Cloud Console, va em **APIs & Services** > **OAuth consent screen**
2. Selecione **External** (ou **Internal** se for Google Workspace)
3. Preencha:
   - **App name**: nome do seu projeto (ex: "Email Agent")
   - **User support email**: seu email
   - **Developer contact**: seu email
4. Em **Scopes**, adicione:
   - `https://www.googleapis.com/auth/gmail.readonly`
   - `https://www.googleapis.com/auth/gmail.modify`
   - `https://www.googleapis.com/auth/gmail.compose`
5. Em **Test users**, adicione o email Gmail que sera monitorado
6. Clique em **Salvar**

> **Importante:** Enquanto o app estiver em modo "Testing", somente os emails listados em Test Users podem autorizar. Para uso em producao, publique o app.

#### 3.5 Criar credenciais OAuth 2.0

1. Va em **APIs & Services** > **Credentials**
2. Clique em **Create Credentials** > **OAuth client ID**
3. Tipo de aplicacao: **Desktop App**
4. Nome: qualquer (ex: "Email Agent Desktop")
5. Clique em **Criar**
6. Clique em **Download JSON**
7. Salve o arquivo como `credentials/client_secret.json` na raiz do projeto:
   ```bash
   mkdir -p credentials
   # Copie o JSON baixado para credentials/client_secret.json
   ```

#### 3.6 Criar Topic do Pub/Sub

```bash
gcloud pubsub topics create gmail-watch --project=SEU_PROJECT_ID
```

#### 3.7 Dar permissao ao Gmail no Topic

```bash
gcloud pubsub topics add-iam-policy-binding gmail-watch \
  --project=SEU_PROJECT_ID \
  --member="serviceAccount:gmail-api-push@system.gserviceaccount.com" \
  --role="roles/pubsub.publisher"
```

#### 3.8 Gerar token de seguranca do webhook

```bash
# Linux/Mac:
openssl rand -hex 16
# Exemplo de saida: 63e0299250b544008b177efa047822be
```

Guarde esse token — ele sera usado na subscription do Pub/Sub e no `.env` como `GMAIL_HOOK_TOKEN_1`.

#### 3.9 Criar Subscription do Pub/Sub

A subscription conecta o Pub/Sub ao seu agente via Tailscale Funnel:

```bash
gcloud pubsub subscriptions create gmail-sub \
  --topic=gmail-watch \
  --project=SEU_PROJECT_ID \
  --push-endpoint="https://SEU_HOSTNAME.tail-xxxxx.ts.net/hooks/gmail?token=SEU_TOKEN_HEX" \
  --ack-deadline=60
```

> Substitua `SEU_HOSTNAME.tail-xxxxx.ts.net` pela URL do Tailscale Funnel (Fase 1.4) e `SEU_TOKEN_HEX` pelo token gerado no passo 3.8.

---

### Fase 4: Configurar APIs de IA

#### 4.1 OpenRouter (LLM principal — classificacao, resumo, decisao)

1. Crie conta em [openrouter.ai](https://openrouter.ai/)
2. Va em **Keys** > **Create Key**
3. Adicione creditos (modelos como `google/gemini-2.5-flash` sao baratos)
4. Copie a key (formato: `sk-or-v1-xxxx`)

#### 4.2 OpenAI (embeddings — memoria vetorial)

> **Opcional:** O agente funciona sem embeddings (Qdrant fica vazio), mas perde a capacidade de buscar emails similares e aprender padroes.

1. Crie conta em [platform.openai.com](https://platform.openai.com/)
2. Va em **API Keys** > **Create new secret key**
3. Adicione creditos (embeddings `text-embedding-3-small` custam ~$0.02/1M tokens)
4. Copie a key (formato: `sk-xxxx`)

---

### Fase 5: Autenticar conta Gmail (OAuth)

#### 5.1 Em servidor com navegador (desktop)

```bash
python scripts/gmail_auth.py --account seu@email.com
```

O navegador abre automaticamente para voce autorizar o acesso.

#### 5.2 Em servidor sem navegador (VPS headless)

```bash
python scripts/gmail_auth.py --account seu@email.com
```

O script detecta que nao ha navegador e exibe um link:

```
Abra este link no navegador do seu PC:
  https://accounts.google.com/o/oauth2/auth?...

Apos autorizar, o navegador vai redirecionar para uma pagina que NAO vai carregar.
Isso e normal! Copie a URL inteira da barra de endereco e cole aqui.
```

1. Copie o link e abra no navegador do seu **PC/celular**
2. Faca login com a conta Gmail que sera monitorada
3. Autorize o acesso
4. O navegador redireciona para `http://localhost/...` — a pagina **nao carrega** (normal!)
5. Copie a **URL inteira** da barra de endereco
6. Cole no terminal da VPS

O token e salvo em `credentials/token_seu@email.com.json`.

> **Dica:** Para monitorar varias contas, repita o processo para cada email.

---

### Fase 6: Rodar o Setup Wizard

Com tudo preparado, clone o projeto e rode o wizard:

```bash
git clone https://github.com/diogenesmendes01/Agente-Email-Openclaw.git
cd Agente-Email-Openclaw

# Copiar o client_secret.json para o projeto
cp /caminho/do/client_secret.json credentials/

python setup_wizard.py
```

O wizard:
1. Cria um ambiente virtual (`.venv/`) automaticamente
2. Instala todas as dependencias Python
3. Guia voce pelas variaveis de ambiente interativamente
4. Cria as tabelas no PostgreSQL
5. Valida o bot do Telegram e descobre o Chat ID
6. Configura as contas Gmail (OAuth + Watch)
7. Importa playbooks (se houver)

> **Reexecutar:** Se precisar reconfigurar algo, rode `python setup_wizard.py` novamente. Ele detecta a instalacao anterior e mostra um menu.

---

### Fase 7: Iniciar o Agente

```bash
# Foreground (desenvolvimento)
.venv/bin/python -m uvicorn orchestrator.main:app --host 0.0.0.0 --port 8787

# Background (producao)
nohup .venv/bin/python -m uvicorn orchestrator.main:app --host 0.0.0.0 --port 8787 > agent.log 2>&1 &
```

O agente registra o webhook do Telegram automaticamente no startup.

#### Via Docker Compose (alternativo)

```bash
docker-compose up -d
```

Isso inicia 3 containers: `postgres`, `qdrant` e `orchestrator`.

---

### Fase 8: Verificar e Testar

#### 8.1 Health check

```bash
curl http://localhost:8787/health
# {"status":"healthy","services":{"postgres":"connected","qdrant":"connected",...}}
```

#### 8.2 Teste de email

1. Envie um email para a conta Gmail monitorada
2. Aguarde ~10 segundos
3. A notificacao deve chegar no Telegram com botoes de acao
4. Verifique os logs: `tail -f agent.log`

#### 8.3 Teste manual via API

```bash
curl -X POST http://localhost:8787/hooks/gmail/test \
  -H "Content-Type: application/json" \
  -d '{"emailId":"SEU_EMAIL_ID","account":"seu@email.com"}'
```

---

### Fase 9: Configurar Playbooks (opcional)

#### Via Telegram (interativo)
```
/config_identidade    # configura empresa (nome, CNPJ, tom, assinatura)
/config_playbook      # cria playbook passo a passo
```

#### Via YAML (bulk import)
```bash
cp playbooks/modelo.yaml.example playbooks/minha-empresa.yaml
# Edite o arquivo com seus dados
python scripts/import_playbooks.py playbooks/minha-empresa.yaml --account-id 1
```

---

### Fase 10: Configurar inicio automatico (producao)

#### Gmail Watch (renovar a cada 7 dias)

```bash
# Cron para renovar o Watch
crontab -e
# Adicione:
0 0 */6 * * cd /caminho/do/projeto && .venv/bin/python scripts/gmail_watch.py --account seu@email.com --topic projects/SEU_PROJECT_ID/topics/gmail-watch
```

#### Systemd Service (auto-restart)

Crie `/etc/systemd/system/email-agent.service`:
```ini
[Unit]
Description=Email Agent - Orchestrator
After=network.target postgresql.service

[Service]
Type=simple
User=root
WorkingDirectory=/caminho/do/Agente-Email-Openclaw
EnvironmentFile=/caminho/do/Agente-Email-Openclaw/.env
ExecStart=/caminho/do/Agente-Email-Openclaw/.venv/bin/python -m uvicorn orchestrator.main:app --host 0.0.0.0 --port 8787
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable email-agent
systemctl start email-agent
```

---

### Resumo dos IDs e tokens necessarios

| Item | Onde obter | Formato | Usado em |
|------|-----------|---------|----------|
| **TELEGRAM_BOT_TOKEN** | @BotFather > `/newbot` | `123456:AAHxxx...` | `.env` |
| **TELEGRAM_CHAT_ID** | `getUpdates` do bot | `-100xxxxxxxxxx` (negativo) | `.env` |
| **TELEGRAM_ALERT_USER_ID** | @userinfobot | `947563152` (positivo) | `.env` |
| **OPENROUTER_API_KEY** | openrouter.ai > Keys | `sk-or-v1-xxx` | `.env` |
| **OPENAI_API_KEY** | platform.openai.com > API Keys | `sk-xxx` | `.env` |
| **Google Project ID** | Google Cloud Console | `email-agent-493213` | comandos `gcloud` |
| **GMAIL_HOOK_TOKEN_1** | `openssl rand -hex 16` | `63e029...` | `.env` + Pub/Sub subscription |
| **client_secret.json** | Google Cloud > Credentials > OAuth | JSON file | `credentials/` |
| **DATABASE_URL** | Criado no passo 1.2 | `postgresql://user:pass@host/db` | `.env` |
| **FUNNEL_BASE_URL** | Tailscale Funnel | `https://host.tail-xxx.ts.net` | `.env` + Pub/Sub subscription |

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
 6. Classificar com LLM (prompt em 3 camadas, com validacao pos-LLM + retry se necessario)
 7. Check playbooks: se match com auto_respond, gerar e enviar resposta
 8. Resumir com LLM (validacao semantica; retry se resumo for generico)
 9. Decidir acao com LLM (inclui `acao_usuario` — instrucao imperativa citando valores/prazos)
    - Validacao anti-invencao: se o rascunho cita valor/data que NAO esta no email, rascunho e removido
10. Persistir decisao (PostgreSQL + Qdrant) + log de qualidade em `llm_quality_log`
11. Executar acao (arquivar/criar task/rascunho)
12. Notificar Telegram com botoes interativos (AÇÃO NECESSÁRIA exibe `acao_usuario`, não `justificativa`)
13. A cada N emails: disparar ciclo de aprendizado
14. Metricas + retry em caso de falha
```

## Testes

```bash
# Rodar todos os testes (308+ testes)
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

O banco possui 18 tabelas, criadas automaticamente pelo `sql/schema.sql`:

| Tabela | Descricao |
|--------|-----------|
| `accounts` | Contas Gmail com config (VIPs, telegram_topic, llm_model override) |
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
| `llm_quality_log` | Telemetria de validacao pos-LLM (flags, retries, tokens por tentativa, fallback_used) |
| `pdf_passwords` | Senhas de PDFs protegidos (Fernet-encrypted, por pattern de remetente) |
| `account_documents` | CPF/CNPJ/data de nascimento da conta (Fernet-encrypted, opt-in, usado so para inferir senhas de PDF) |
| `account_prompt_config` | Config estruturada da Camada 3 por conta (JSONB: tom, instrucoes extras, categorias, tamanho, instrucoes livres) |

### Migracoes (bancos existentes)

Se o banco ja existe com tabelas de versoes anteriores, rode as migracoes:

```bash
# Phase 3+4: tabelas de pending_actions, company_profiles, playbooks, etc.
psql $DATABASE_URL -f sql/migrations/001_phase3_4_tables.sql

# Idempotencia: dedup de decisions/playbooks + constraints UNIQUE + NOT NULL
psql $DATABASE_URL -f sql/migrations/002_idempotency_constraints.sql

# Setup wizard + metrics + LLM per-account + PDF/prompt features
psql $DATABASE_URL -f sql/migrations/003_accounts_owner_name.sql
psql $DATABASE_URL -f sql/migrations/004_metrics_cost_usd.sql
psql $DATABASE_URL -f sql/migrations/005_accounts_llm_model.sql
psql $DATABASE_URL -f sql/migrations/006_llm_quality_log.sql       # validacao pos-LLM
psql $DATABASE_URL -f sql/migrations/007_pdf_passwords.sql         # senhas de PDF + account_documents
psql $DATABASE_URL -f sql/migrations/008_account_prompt_config.sql # Camada 3 dos prompts
```

Os scripts sao idempotentes (`IF NOT EXISTS`, dedup antes de criar constraints, `ADD COLUMN IF NOT EXISTS`), entao e seguro rodar mais de uma vez. O `setup_wizard.py` aplica todas as migracoes automaticamente.

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
