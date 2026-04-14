"""Tests for bug fixes round 2 — infra reliability."""
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ═══════════════════════════════════════════════════════════════
# Fix #2 — DB-level idempotency (log_decision ON CONFLICT)
# ═══════════════════════════════════════════════════════════════


def test_log_decision_sql_has_on_conflict():
    """log_decision should use ON CONFLICT DO NOTHING to prevent duplicate decisions."""
    import inspect
    from orchestrator.services.database_service import DatabaseService
    source = inspect.getsource(DatabaseService.log_decision)
    assert "ON CONFLICT" in source, "log_decision must use ON CONFLICT for idempotency"
    assert "account_id, email_id" in source, "ON CONFLICT should be on (account_id, email_id)"


def test_schema_decisions_unique_constraint():
    """schema.sql should have UNIQUE(account_id, email_id) on decisions."""
    schema_path = os.path.join(os.path.dirname(__file__), "..", "sql", "schema.sql")
    with open(schema_path) as f:
        schema = f.read()
    # Should appear inside the decisions CREATE TABLE
    decisions_block = schema[schema.index("CREATE TABLE decisions"):schema.index(");", schema.index("CREATE TABLE decisions"))]
    assert "UNIQUE(account_id, email_id)" in decisions_block


# ═══════════════════════════════════════════════════════════════
# Fix #3 — Job queue FOR UPDATE SKIP LOCKED
# ═══════════════════════════════════════════════════════════════


def test_job_queue_get_pending_uses_skip_locked():
    """get_pending should use FOR UPDATE SKIP LOCKED to prevent double-pickup."""
    import inspect
    from orchestrator.services.job_queue import JobQueue
    source = inspect.getsource(JobQueue.get_pending)
    assert "FOR UPDATE SKIP LOCKED" in source, "get_pending must lock rows"
    assert "processing" in source, "get_pending should set status to 'processing'"


def test_job_queue_mark_failed_resets_to_pending():
    """mark_failed should reset status to 'pending' (not leave it as 'processing')."""
    import inspect
    from orchestrator.services.job_queue import JobQueue
    source = inspect.getsource(JobQueue.mark_failed)
    assert "status = 'pending'" in source, "mark_failed should reset status to pending for next retry"


# ═══════════════════════════════════════════════════════════════
# Fix #4 — import_playbooks.py idempotent
# ═══════════════════════════════════════════════════════════════


def test_schema_playbooks_unique_constraint():
    """schema.sql should have UNIQUE(company_id, trigger_description) on playbooks."""
    schema_path = os.path.join(os.path.dirname(__file__), "..", "sql", "schema.sql")
    with open(schema_path) as f:
        schema = f.read()
    playbooks_block = schema[schema.index("CREATE TABLE playbooks"):schema.index(");", schema.index("CREATE TABLE playbooks"))]
    assert "UNIQUE(company_id, trigger_description)" in playbooks_block


def test_import_script_uses_on_conflict():
    """import_playbooks.py should use ON CONFLICT for idempotent imports."""
    script_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "import_playbooks.py")
    with open(script_path) as f:
        source = f.read()
    assert "ON CONFLICT" in source, "import_playbooks should use ON CONFLICT"
    assert "company_id, trigger_description" in source, "ON CONFLICT should match the unique constraint"


def test_create_playbook_uses_on_conflict():
    """DatabaseService.create_playbook should use ON CONFLICT for idempotency."""
    import inspect
    from orchestrator.services.database_service import DatabaseService
    source = inspect.getsource(DatabaseService.create_playbook)
    assert "ON CONFLICT" in source, "create_playbook must use ON CONFLICT"


# ═══════════════════════════════════════════════════════════════
# Fix #5 — Gmail accounts gap (continue instead of break)
# ═══════════════════════════════════════════════════════════════


def test_gmail_accounts_gap_in_numbering():
    """Settings should load accounts even with gaps in numbering (e.g. 1, 3 without 2)."""
    env = {
        "OPENROUTER_API_KEY": "sk-or-test",
        "OPENAI_API_KEY": "sk-test",
        "TELEGRAM_BOT_TOKEN": "123:ABC",
        "TELEGRAM_CHAT_ID": "-100123",
        "TELEGRAM_ALLOWED_USER_IDS": "111",
        "TELEGRAM_WEBHOOK_SECRET": "secret",
        "TELEGRAM_ALERT_USER_ID": "111",
        "DATABASE_URL": "postgresql://user:pass@localhost:5432/test",
        "FUNNEL_BASE_URL": "https://machine.ts.net",
        "GMAIL_ACCOUNT_1": "first@gmail.com",
        "GMAIL_HOOK_TOKEN_1": "token1",
        # Gap: no GMAIL_ACCOUNT_2
        "GMAIL_ACCOUNT_3": "third@gmail.com",
        "GMAIL_HOOK_TOKEN_3": "token3",
    }
    with patch.dict(os.environ, env, clear=False):
        from orchestrator.settings import Settings
        s = Settings()
        assert len(s.gmail_accounts) == 2, f"Expected 2 accounts, got {len(s.gmail_accounts)}"
        assert "first@gmail.com" in s.gmail_accounts
        assert "third@gmail.com" in s.gmail_accounts


def test_gmail_accounts_warns_on_missing_token():
    """Settings should warn when GMAIL_ACCOUNT_N is set but GMAIL_HOOK_TOKEN_N is missing."""
    env = {
        "OPENROUTER_API_KEY": "sk-or-test",
        "OPENAI_API_KEY": "sk-test",
        "TELEGRAM_BOT_TOKEN": "123:ABC",
        "TELEGRAM_CHAT_ID": "-100123",
        "TELEGRAM_ALLOWED_USER_IDS": "111",
        "TELEGRAM_WEBHOOK_SECRET": "secret",
        "TELEGRAM_ALERT_USER_ID": "111",
        "DATABASE_URL": "postgresql://user:pass@localhost:5432/test",
        "FUNNEL_BASE_URL": "https://machine.ts.net",
        "GMAIL_ACCOUNT_1": "test@gmail.com",
        "GMAIL_HOOK_TOKEN_1": "token1",
        "GMAIL_ACCOUNT_2": "orphan@gmail.com",
        # No GMAIL_HOOK_TOKEN_2
    }
    import logging
    with patch.dict(os.environ, env, clear=False):
        with patch("orchestrator.settings.logger") as mock_logger:
            from orchestrator.settings import Settings
            s = Settings()
            assert "orphan@gmail.com" not in s.gmail_accounts
            mock_logger.warning.assert_called()


# ═══════════════════════════════════════════════════════════════
# Fix #6 — gmail_auth.py validates authenticated account
# ═══════════════════════════════════════════════════════════════


def test_gmail_auth_validates_account():
    """gmail_auth.py should call Gmail API to verify the authenticated email matches --account."""
    script_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "gmail_auth.py")
    with open(script_path) as f:
        source = f.read()
    assert "getProfile" in source, "Should call Gmail getProfile to verify account"
    assert "emailAddress" in source, "Should check emailAddress from profile"
    assert "NAO foi salvo" in source or "not saved" in source.lower(), "Should refuse to save on mismatch"


# ═══════════════════════════════════════════════════════════════
# Fix #1 — seed_account.py exists and is idempotent
# ═══════════════════════════════════════════════════════════════


def test_seed_account_script_exists():
    """seed_account.py should exist in scripts/."""
    script_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "seed_account.py")
    assert os.path.isfile(script_path), "scripts/seed_account.py should exist"


def test_seed_account_uses_on_conflict():
    """seed_account.py should use ON CONFLICT for idempotent upsert."""
    script_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "seed_account.py")
    with open(script_path) as f:
        source = f.read()
    assert "ON CONFLICT" in source, "seed_account should be idempotent via ON CONFLICT"
    assert "--email" in source, "Should accept --email argument"
    assert "--hook-token-env" in source, "Should accept --hook-token-env argument"


# ═══════════════════════════════════════════════════════════════
# Fix #7 — README documents seed_account step
# ═══════════════════════════════════════════════════════════════


def test_readme_mentions_seed_account():
    """README should document the seed_account.py step as mandatory."""
    readme_path = os.path.join(os.path.dirname(__file__), "..", "README.md")
    with open(readme_path) as f:
        readme = f.read()
    assert "seed_account" in readme, "README must mention seed_account.py"
    assert "OBRIGATORIO" in readme or "obrigatorio" in readme, "README must mark it as mandatory"


# ═══════════════════════════════════════════════════════════════
# Migration script exists
# ═══════════════════════════════════════════════════════════════


def test_migration_002_exists():
    """Migration 002 for idempotency constraints should exist."""
    migration_path = os.path.join(os.path.dirname(__file__), "..", "sql", "migrations", "002_idempotency_constraints.sql")
    assert os.path.isfile(migration_path)
    with open(migration_path) as f:
        content = f.read()
    assert "decisions_account_id_email_id_key" in content
    assert "playbooks_company_id_trigger_description_key" in content
