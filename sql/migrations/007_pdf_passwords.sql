-- Migration 006: PDF password storage and account documents
-- Enables the agent to decrypt password-protected PDFs attached to emails.
--
-- Two tables:
--   pdf_passwords       — explicit passwords cadastradas pelo usuário
--                         (encrypted with Fernet key PDF_PASSWORD_KEY).
--   account_documents   — opt-in CPF/CNPJ/birthdate used to *infer* passwords
--                         from email body hints (e.g. "sua senha é o CPF").
--
-- Both tables store secrets encrypted at rest. Never store plaintext.

CREATE TABLE IF NOT EXISTS pdf_passwords (
    id SERIAL PRIMARY KEY,
    account_id INT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    sender_pattern TEXT NOT NULL,        -- "*@bank.com" or literal "foo@bar.com"
    password_encrypted TEXT NOT NULL,
    label TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_used_at TIMESTAMPTZ,
    use_count INT DEFAULT 0,
    locked_until TIMESTAMPTZ,
    UNIQUE (account_id, sender_pattern, password_encrypted)
);

CREATE INDEX IF NOT EXISTS idx_pdf_passwords_account_pattern
    ON pdf_passwords(account_id, sender_pattern);

CREATE TABLE IF NOT EXISTS account_documents (
    account_id INT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    cpf_encrypted TEXT,
    cnpj_encrypted TEXT,
    birthdate_encrypted TEXT,           -- YYYY-MM-DD plaintext before encrypt
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
