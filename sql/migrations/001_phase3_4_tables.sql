-- Migration 001: Phase 3 (Pending Actions) + Phase 4 (Playbooks Multi-Empresa)
-- For existing databases that already have Phase 1-2 tables.
-- Run: psql $DATABASE_URL -f sql/migrations/001_phase3_4_tables.sql
--
-- Safe to re-run: uses CREATE TABLE IF NOT EXISTS and IF NOT EXISTS for columns/indexes.

-- ── Phase 3: Pending Actions ──

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

-- If pending_actions existed before topic_id was added:
ALTER TABLE pending_actions ADD COLUMN IF NOT EXISTS topic_id BIGINT;

CREATE INDEX IF NOT EXISTS idx_pending_email ON pending_actions(email_id);
CREATE INDEX IF NOT EXISTS idx_pending_expires ON pending_actions(expires_at);
CREATE INDEX IF NOT EXISTS idx_pending_topic ON pending_actions(topic_id);

-- ── Phase 4: Playbooks Multi-Empresa ──

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
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
