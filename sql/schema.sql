-- sql/schema.sql
-- Email Agent Platform — PostgreSQL Schema

CREATE TABLE IF NOT EXISTS accounts (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    owner_name VARCHAR(255),
    hook_token_env VARCHAR(100) NOT NULL,
    oauth_token_path VARCHAR(255),
    telegram_topic_id BIGINT,
    learning_counter INT DEFAULT 0,
    llm_model VARCHAR(255),
    llm_fallback_model VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS vip_list (
    id SERIAL PRIMARY KEY,
    account_id INT REFERENCES accounts(id) ON DELETE CASCADE,
    sender_email VARCHAR(255) NOT NULL,
    sender_name VARCHAR(255),
    min_urgency VARCHAR(20) DEFAULT 'high',
    added_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(account_id, sender_email)
);

CREATE TABLE IF NOT EXISTS blacklist (
    id SERIAL PRIMARY KEY,
    account_id INT REFERENCES accounts(id) ON DELETE CASCADE,
    sender_email VARCHAR(255) NOT NULL,
    reason VARCHAR(255),
    added_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(account_id, sender_email)
);

CREATE TABLE IF NOT EXISTS feedback (
    id SERIAL PRIMARY KEY,
    account_id INT REFERENCES accounts(id) ON DELETE CASCADE,
    email_id VARCHAR(100) NOT NULL,
    sender VARCHAR(255),
    original_urgency VARCHAR(20),
    corrected_urgency VARCHAR(20),
    keywords TEXT[],
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS decisions (
    id SERIAL PRIMARY KEY,
    account_id INT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    email_id VARCHAR(100) NOT NULL,
    subject TEXT,
    sender VARCHAR(255),
    classification VARCHAR(50),
    priority VARCHAR(20),
    category VARCHAR(50),
    action VARCHAR(50),
    summary TEXT,
    reasoning_tokens INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT idx_decisions_account_email UNIQUE(account_id, email_id)
);

CREATE TABLE IF NOT EXISTS tasks (
    id SERIAL PRIMARY KEY,
    account_id INT REFERENCES accounts(id) ON DELETE CASCADE,
    email_id VARCHAR(100),
    title TEXT NOT NULL,
    priority VARCHAR(20) DEFAULT 'Média',
    status VARCHAR(20) DEFAULT 'Pendente',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS history_ids (
    account_id INT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    history_id VARCHAR(50) NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Phase 2 tables (forward-planned — included here so the schema file is
-- complete and Phase 2 only needs ALTER/migration, not a second init script.
-- If Phase 2 design changes, update this file before deploying Phase 2.)

CREATE TABLE IF NOT EXISTS metrics (
    id SERIAL PRIMARY KEY,
    request_id VARCHAR(8),
    account_id INT REFERENCES accounts(id),
    event VARCHAR(50) NOT NULL,
    service VARCHAR(30),
    latency_ms INT,
    tokens_used INT,
    cost_usd NUMERIC(10,6) DEFAULT 0,
    success BOOLEAN DEFAULT true,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_metrics_created ON metrics(created_at);
CREATE INDEX IF NOT EXISTS idx_metrics_event ON metrics(event);
CREATE INDEX IF NOT EXISTS idx_metrics_account_created ON metrics(account_id, created_at);

CREATE TABLE IF NOT EXISTS failed_jobs (
    id SERIAL PRIMARY KEY,
    account_id INT REFERENCES accounts(id),
    job_type VARCHAR(50) NOT NULL,
    payload JSONB NOT NULL,
    attempts INT DEFAULT 0,
    max_attempts INT DEFAULT 5,
    last_error TEXT,
    next_retry_at TIMESTAMPTZ,
    status VARCHAR(20) DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_failed_jobs_status ON failed_jobs(status, next_retry_at);

-- Phase 3: Pending Actions (replaces pending_actions.json and pending_replies.json)

CREATE TABLE IF NOT EXISTS pending_actions (
    id SERIAL PRIMARY KEY,
    account_id INT REFERENCES accounts(id),
    email_id VARCHAR(100) NOT NULL,
    action_type VARCHAR(50) NOT NULL,
    actor_id BIGINT NOT NULL,
    chat_id BIGINT,
    topic_id BIGINT,
    message_id BIGINT,
    state JSONB DEFAULT '{}',
    expires_at TIMESTAMPTZ DEFAULT NOW() + INTERVAL '10 minutes',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pending_email ON pending_actions(email_id);
CREATE INDEX IF NOT EXISTS idx_pending_expires ON pending_actions(expires_at);

-- Phase 4: Playbooks Multi-Empresa

CREATE TABLE IF NOT EXISTS company_profiles (
    id SERIAL PRIMARY KEY,
    account_id INT UNIQUE REFERENCES accounts(id) ON DELETE CASCADE,
    company_name VARCHAR(255) NOT NULL,
    cnpj VARCHAR(20),
    tone TEXT,
    signature TEXT,
    whatsapp_url VARCHAR(500),
    extra_config JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS clients (
    id SERIAL PRIMARY KEY,
    company_id INT REFERENCES company_profiles(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    contacts TEXT,
    active_project VARCHAR(255),
    priority VARCHAR(20) DEFAULT 'Média',
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS domain_rules (
    id SERIAL PRIMARY KEY,
    company_id INT REFERENCES company_profiles(id) ON DELETE CASCADE,
    domain VARCHAR(255) NOT NULL,
    category VARCHAR(50),
    min_priority VARCHAR(20),
    default_action VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(company_id, domain)
);

CREATE TABLE IF NOT EXISTS playbooks (
    id SERIAL PRIMARY KEY,
    company_id INT REFERENCES company_profiles(id) ON DELETE CASCADE,
    trigger_description TEXT NOT NULL,
    auto_respond BOOLEAN DEFAULT true,
    response_template TEXT NOT NULL,
    priority INT DEFAULT 0,
    active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT idx_playbooks_company_trigger UNIQUE(company_id, trigger_description)
);

CREATE TABLE IF NOT EXISTS llm_quality_log (
    id SERIAL PRIMARY KEY,
    account_id INT REFERENCES accounts(id) ON DELETE SET NULL,
    email_message_id TEXT,
    kind TEXT NOT NULL,
    model TEXT,
    retries INT DEFAULT 0,
    flags TEXT[],
    json_parse_failed BOOLEAN DEFAULT FALSE,
    schema_valid BOOLEAN DEFAULT TRUE,
    fallback_used BOOLEAN DEFAULT FALSE,
    prompt_tokens INT,
    completion_tokens INT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_llm_quality_log_account_kind
    ON llm_quality_log(account_id, kind, created_at DESC);
