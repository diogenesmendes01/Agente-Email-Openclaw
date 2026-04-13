-- sql/schema.sql
-- Email Agent Platform — PostgreSQL Schema

CREATE TABLE accounts (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    hook_token_env VARCHAR(100) NOT NULL,
    oauth_token_path VARCHAR(255),
    telegram_topic_id BIGINT,
    learning_counter INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE vip_list (
    id SERIAL PRIMARY KEY,
    account_id INT REFERENCES accounts(id) ON DELETE CASCADE,
    sender_email VARCHAR(255) NOT NULL,
    sender_name VARCHAR(255),
    min_urgency VARCHAR(20) DEFAULT 'high',
    added_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(account_id, sender_email)
);

CREATE TABLE blacklist (
    id SERIAL PRIMARY KEY,
    account_id INT REFERENCES accounts(id) ON DELETE CASCADE,
    sender_email VARCHAR(255) NOT NULL,
    reason VARCHAR(255),
    added_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(account_id, sender_email)
);

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

CREATE TABLE tasks (
    id SERIAL PRIMARY KEY,
    account_id INT REFERENCES accounts(id) ON DELETE CASCADE,
    email_id VARCHAR(100),
    title TEXT NOT NULL,
    priority VARCHAR(20) DEFAULT 'Média',
    status VARCHAR(20) DEFAULT 'Pendente',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE history_ids (
    account_id INT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    history_id VARCHAR(50) NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Phase 2 tables (forward-planned — included here so the schema file is
-- complete and Phase 2 only needs ALTER/migration, not a second init script.
-- If Phase 2 design changes, update this file before deploying Phase 2.)

CREATE TABLE metrics (
    id SERIAL PRIMARY KEY,
    request_id VARCHAR(8),
    account_id INT REFERENCES accounts(id),
    event VARCHAR(50) NOT NULL,
    service VARCHAR(30),
    latency_ms INT,
    tokens_used INT,
    success BOOLEAN DEFAULT true,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_metrics_created ON metrics(created_at);
CREATE INDEX idx_metrics_event ON metrics(event);

CREATE TABLE failed_jobs (
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

CREATE INDEX idx_failed_jobs_status ON failed_jobs(status, next_retry_at);
