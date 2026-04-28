-- Migration 009: marca decisões geradas para emails não-respondíveis.
-- Permite auditoria + base para tuning futuro do classifier.

ALTER TABLE decisions
    ADD COLUMN IF NOT EXISTS no_reply_detected BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN decisions.no_reply_detected IS
    'True quando reply_policy bateu (sender no-reply OU categoria nao-respondivel). Quando true, acao=rascunho foi rebaixada para notificar.';

CREATE INDEX IF NOT EXISTS idx_decisions_no_reply
    ON decisions(no_reply_detected)
    WHERE no_reply_detected = TRUE;
