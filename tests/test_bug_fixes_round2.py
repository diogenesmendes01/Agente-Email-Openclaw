"""Tests for bug fixes round 2 — infra reliability."""
import os
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock


# ═══════════════════════════════════════════════════════════════
# Fix #2 — Atomic claim pattern (claim_email + update_decision)
# ═══════════════════════════════════════════════════════════════


def test_claim_email_sql_has_on_conflict():
    """claim_email should use ON CONFLICT DO NOTHING for atomic dedup."""
    import inspect
    from orchestrator.services.database_service import DatabaseService
    source = inspect.getsource(DatabaseService.claim_email)
    assert "ON CONFLICT" in source, "claim_email must use ON CONFLICT for idempotency"
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


def test_migration_002_has_not_null_for_account_id():
    """Migration 002 should enforce NOT NULL on decisions.account_id."""
    migration_path = os.path.join(os.path.dirname(__file__), "..", "sql", "migrations", "002_idempotency_constraints.sql")
    with open(migration_path) as f:
        content = f.read()
    assert "SET NOT NULL" in content, "Migration must add NOT NULL to decisions.account_id"
    assert "account_id IS NULL" in content, "Migration must delete orphan NULL rows first"


def test_schema_decisions_account_id_not_null():
    """schema.sql should have account_id as NOT NULL in decisions table."""
    schema_path = os.path.join(os.path.dirname(__file__), "..", "sql", "schema.sql")
    with open(schema_path) as f:
        schema = f.read()
    decisions_block = schema[schema.index("CREATE TABLE decisions"):schema.index(");", schema.index("CREATE TABLE decisions"))]
    assert "account_id INT NOT NULL" in decisions_block, "decisions.account_id must be NOT NULL"


# ═══════════════════════════════════════════════════════════════
# Behavioral tests — PR review items
# ═══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_claim_email_returns_none_on_duplicate(mock_pool):
    """claim_email should return None when ON CONFLICT fires (duplicate)."""
    pool, conn = mock_pool
    conn.fetchrow.return_value = None  # ON CONFLICT DO NOTHING → no RETURNING

    from orchestrator.services.database_service import DatabaseService
    db = DatabaseService(pool)
    result = await db.claim_email(1, "dup123")
    assert result is None, "claim_email must return None for duplicates"


@pytest.mark.asyncio
async def test_claim_email_returns_id_on_success(mock_pool):
    """claim_email should return the new decision id when claim succeeds."""
    pool, conn = mock_pool
    conn.fetchrow.return_value = {"id": 42}

    from orchestrator.services.database_service import DatabaseService
    db = DatabaseService(pool)
    result = await db.claim_email(1, "new123")
    assert result == 42


def test_email_processor_uses_atomic_claim():
    """EmailProcessor must use claim_email (not decision_exists) for concurrency safety."""
    import inspect
    from orchestrator.handlers.email_processor import EmailProcessor
    source = inspect.getsource(EmailProcessor.process_email)
    assert "claim_email" in source, "EmailProcessor must use claim_email for atomic dedup"
    assert "decision_exists" not in source, "decision_exists is not concurrency-safe — use claim_email"
    assert '"duplicate"' in source, "EmailProcessor must set status to 'duplicate' on claim failure"


@pytest.mark.asyncio
async def test_job_queue_reap_stuck_processing(mock_pool):
    """reap_stuck_processing should reset stuck jobs back to pending."""
    pool, conn = mock_pool
    conn.execute.return_value = "UPDATE 2"

    from orchestrator.services.job_queue import JobQueue
    jq = JobQueue(pool)
    count = await jq.reap_stuck_processing(timeout_minutes=15)
    assert count == 2
    conn.execute.assert_called_once()
    call_sql = conn.execute.call_args[0][0]
    assert "status = 'pending'" in call_sql
    assert "status = 'processing'" in call_sql


@pytest.mark.asyncio
async def test_get_pending_uses_transaction(mock_pool):
    """get_pending should run inside a transaction for atomic lock+update."""
    pool, conn = mock_pool
    conn.fetch.return_value = [{"id": 1, "job_type": "test", "payload": "{}"}]
    conn.execute.return_value = "UPDATE 1"

    from orchestrator.services.job_queue import JobQueue
    jq = JobQueue(pool)
    jobs = await jq.get_pending(limit=5)
    assert len(jobs) == 1
    # transaction() should have been called
    conn.transaction.assert_called_once()


def test_gmail_auth_validates_even_with_valid_token():
    """gmail_auth.py should call getProfile even when creds are already valid (no early return)."""
    script_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "gmail_auth.py")
    with open(script_path) as f:
        source = f.read()
    # The old code had: if creds and creds.valid: ... return
    # The new code should NOT have an early return before getProfile
    lines = source.split("\n")
    found_valid_check = False
    for i, line in enumerate(lines):
        if "creds.valid" in line and "creds and creds.valid" in line:
            found_valid_check = True
            # Check the next few lines don't have a bare 'return'
            for j in range(i + 1, min(i + 5, len(lines))):
                stripped = lines[j].strip()
                if stripped == "return":
                    pytest.fail("gmail_auth.py has early return after creds.valid — validation is skipped")
                if stripped and not stripped.startswith("#"):
                    break
    assert found_valid_check, "Should have a creds.valid check"


def test_atomic_claim_before_playbook():
    """EmailProcessor must call claim_email BEFORE the playbook block."""
    import inspect
    from orchestrator.handlers.email_processor import EmailProcessor
    source = inspect.getsource(EmailProcessor.process_email)
    claim_pos = source.index("claim_email")
    playbook_pos = source.index("playbook_service")
    assert claim_pos < playbook_pos, (
        "claim_email must come before playbook_service usage "
        "to prevent concurrent duplicate auto-responses"
    )


def test_reap_stuck_called_in_retry_worker():
    """retry_worker in main.py should call reap_stuck_processing."""
    main_path = os.path.join(os.path.dirname(__file__), "..", "orchestrator", "main.py")
    with open(main_path) as f:
        source = f.read()
    # Find the retry_worker function
    assert "reap_stuck_processing" in source, "main.py must call reap_stuck_processing"
    # It should be inside retry_worker
    worker_start = source.index("async def retry_worker")
    worker_end = source.index("async def maintenance_worker")
    worker_body = source[worker_start:worker_end]
    assert "reap_stuck_processing" in worker_body, (
        "reap_stuck_processing must be called inside retry_worker"
    )


def test_get_pending_updates_next_retry_at_on_claim():
    """get_pending must set next_retry_at = NOW() when claiming so reaper measures from claim time."""
    import inspect
    from orchestrator.services.job_queue import JobQueue
    source = inspect.getsource(JobQueue.get_pending)
    assert "next_retry_at = NOW()" in source, (
        "get_pending must update next_retry_at when claiming a job, "
        "otherwise the reaper uses the original enqueue time and may reap active jobs"
    )


@pytest.mark.asyncio
async def test_claim_released_on_pre_sideeffect_error():
    """If processing fails BEFORE side effects, release claim so retries work."""
    from orchestrator.handlers.email_processor import EmailProcessor
    db = AsyncMock()
    qdrant = MagicMock()
    qdrant.is_connected.return_value = False
    llm = AsyncMock()
    gmail = AsyncMock()
    telegram = AsyncMock()

    proc = EmailProcessor(db, qdrant, llm, gmail, telegram)

    gmail.get_email.return_value = {
        "id": "em1", "from": "s@t.com", "from_email": "s@t.com",
        "from_name": "S", "subject": "Sub", "body": "Hello",
        "body_clean": "", "attachments": [], "threadId": "t1", "date": "2026-04-14",
    }
    db.get_account.return_value = {"id": 1}
    db.claim_email.return_value = 99  # claim succeeds

    # Make classify blow up BEFORE any side effects
    llm.classify_email.side_effect = RuntimeError("LLM unavailable")

    result = await proc.process_email("em1", "u@t.com")
    assert result["status"] == "error"
    # The skeleton row must have been released (safe to retry)
    db.release_claim.assert_called_once_with(99)
    # update_decision should NOT have been called (no finalization)
    db.update_decision.assert_not_called()


@pytest.mark.asyncio
async def test_claim_kept_on_post_sideeffect_error():
    """If processing fails AFTER side effects (e.g. send_reply done, then Telegram fails),
    the claim must NOT be released — retrying would duplicate the auto-response."""
    from orchestrator.handlers.email_processor import EmailProcessor
    db = AsyncMock()
    qdrant = MagicMock()
    qdrant.is_connected.return_value = False
    llm = AsyncMock()
    gmail = AsyncMock()
    telegram = AsyncMock()
    playbook_svc = AsyncMock()

    proc = EmailProcessor(db, qdrant, llm, gmail, telegram, playbook_service=playbook_svc)

    gmail.get_email.return_value = {
        "id": "em1", "from": "c@t.com", "from_email": "c@t.com",
        "from_name": "Client", "subject": "Boleto", "body": "preciso boleto",
        "body_clean": "", "attachments": [], "threadId": "t1", "date": "2026-04-14",
    }
    db.get_account.return_value = {"id": 1}
    db.claim_email.return_value = 99

    llm.classify_email.return_value = {
        "prioridade": "Média", "importante": True, "confianca": 0.8,
        "categoria": "financeiro", "reasoning_tokens": 10,
    }

    # Playbook matches and send_reply succeeds (side effect executed!)
    playbook_svc.match.return_value = {
        "playbook_id": 1, "template": "...", "trigger": "boleto",
        "auto_respond": True, "confidence": 0.9,
        "company": {"company_name": "CW", "tone": "formal", "signature": "Att"},
    }
    playbook_svc.generate_response.return_value = "Reply text"
    gmail.send_reply.return_value = "sent_msg_id"  # Side effect succeeded

    # Then summarize_email blows up
    llm.summarize_email.side_effect = RuntimeError("LLM quota exceeded")

    result = await proc.process_email("em1", "u@t.com")
    assert result["status"] == "error"
    # Claim must NOT be released — send_reply already fired
    db.release_claim.assert_not_called()
    # Decision row should be finalized with partial data
    db.update_decision.assert_called_once()
    call_data = db.update_decision.call_args[0][1]
    assert "erro_parcial" in call_data.get("acao", "") or "erro" in call_data.get("resumo", "").lower()


@pytest.mark.asyncio
async def test_skipped_email_fills_decision_row():
    """When email is skipped (not important), the decision row must be filled, not left as skeleton."""
    from orchestrator.handlers.email_processor import EmailProcessor
    db = AsyncMock()
    qdrant = MagicMock()
    qdrant.is_connected.return_value = False
    llm = AsyncMock()
    gmail = AsyncMock()
    telegram = AsyncMock()

    proc = EmailProcessor(db, qdrant, llm, gmail, telegram)

    gmail.get_email.return_value = {
        "id": "em1", "from": "news@spam.com", "from_email": "news@spam.com",
        "from_name": "Newsletter", "subject": "Weekly digest", "body": "Unsubscribe",
        "body_clean": "", "attachments": [], "threadId": "t1", "date": "2026-04-14",
    }
    db.get_account.return_value = {"id": 1}
    db.claim_email.return_value = 77  # claim succeeds

    # Classify as not important with high confidence → triggers "skipped" path
    llm.classify_email.return_value = {
        "importante": False, "confianca": 0.95,
        "categoria": "newsletter", "prioridade": "Baixa",
        "reasoning_tokens": 50,
    }

    result = await proc.process_email("em1", "u@t.com")
    assert result["status"] == "skipped"
    # The decision row must have been updated with classification data
    db.update_decision.assert_called_once()
    call_args = db.update_decision.call_args
    assert call_args[0][0] == 77  # decision_id
    data = call_args[0][1]
    assert data["acao"] == "ignorar"
    assert data["classificacao"] == "newsletter"


@pytest.mark.asyncio
async def test_release_claim_deletes_row(mock_pool):
    """release_claim should DELETE the skeleton decision row."""
    pool, conn = mock_pool
    from orchestrator.services.database_service import DatabaseService
    db = DatabaseService(pool)
    await db.release_claim(42)
    conn.execute.assert_called_once()
    call_sql = conn.execute.call_args[0][0]
    assert "DELETE" in call_sql
    assert "decisions" in call_sql


def test_gmail_accounts_slot_20():
    """Settings should load GMAIL_ACCOUNT_20 (range must go to 21)."""
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
        "GMAIL_ACCOUNT_20": "slot20@gmail.com",
        "GMAIL_HOOK_TOKEN_20": "token20",
    }
    with patch.dict(os.environ, env, clear=False):
        from orchestrator.settings import Settings
        s = Settings()
        assert "slot20@gmail.com" in s.gmail_accounts, "GMAIL_ACCOUNT_20 must be loaded (range(1, 21))"


# ═══════════════════════════════════════════════════════════════
# Round 5 — side_effects_executed timing + partial error data
# ═══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_notificar_telegram_failure_releases_claim():
    """acao='notificar' + Telegram failure should release claim and allow retry.

    'notificar' has no irreversible side effect inside _execute_action — the
    Telegram notification is idempotent.  So _side_effects_executed must remain
    False, letting the except block release the claim and enqueue a retry.
    """
    from orchestrator.handlers.email_processor import EmailProcessor
    db = AsyncMock()
    qdrant = MagicMock()
    qdrant.is_connected.return_value = False
    llm = AsyncMock()
    gmail = AsyncMock()
    telegram = AsyncMock()

    proc = EmailProcessor(db, qdrant, llm, gmail, telegram)

    gmail.get_email.return_value = {
        "id": "em1", "from": "friend@co.com", "from_email": "friend@co.com",
        "from_name": "Friend", "subject": "Meeting", "body": "Let's sync",
        "body_clean": "", "attachments": [], "threadId": "t1", "date": "2026-04-14",
    }
    db.get_account.return_value = {"id": 1}
    db.claim_email.return_value = 55

    llm.classify_email.return_value = {
        "prioridade": "Média", "importante": True, "confianca": 0.8,
        "categoria": "trabalho", "reasoning_tokens": 10,
    }
    llm.summarize_email.return_value = {"resumo": "Sync meeting", "reasoning_tokens": 5}
    llm.decide_action.return_value = {"acao": "notificar", "reasoning_tokens": 5}

    # Telegram notification blows up AFTER _execute_action succeeds (notificar is a no-op there)
    telegram.send_email_notification.side_effect = RuntimeError("Telegram API timeout")

    result = await proc.process_email("em1", "friend@co.com")
    assert result["status"] == "error"
    # _side_effects_executed should be False for acao='notificar' → claim released
    db.release_claim.assert_called_once_with(55)
    # Decision should NOT be finalized (no partial update — clean retry instead)
    # update_decision was called once in the happy path (step 7) before the error
    # The except block should NOT call it again since _side_effects_executed is False
    assert db.update_decision.call_count == 1, (
        "update_decision should only be called once (step 7), not in the except block"
    )


@pytest.mark.asyncio
async def test_partial_error_preserves_real_subject_and_sender():
    """When side effects fired and a later error occurs, the partial decision
    finalization must use the parsed email's subject/from — not empty strings
    from result['classification'] which doesn't have those fields.
    """
    from orchestrator.handlers.email_processor import EmailProcessor
    db = AsyncMock()
    qdrant = MagicMock()
    qdrant.is_connected.return_value = False
    llm = AsyncMock()
    gmail = AsyncMock()
    telegram = AsyncMock()

    proc = EmailProcessor(db, qdrant, llm, gmail, telegram)

    gmail.get_email.return_value = {
        "id": "em1", "from": "vip@corp.com", "from_email": "vip@corp.com",
        "from_name": "VIP Client", "subject": "Contrato urgente", "body": "Precisamos assinar",
        "body_clean": "", "attachments": [], "threadId": "t1", "date": "2026-04-14",
    }
    db.get_account.return_value = {"id": 1}
    db.claim_email.return_value = 88

    llm.classify_email.return_value = {
        "prioridade": "Alta", "importante": True, "confianca": 0.9,
        "categoria": "trabalho", "reasoning_tokens": 10,
    }
    llm.summarize_email.return_value = {"resumo": "Contrato para assinatura", "reasoning_tokens": 5}
    llm.decide_action.return_value = {"acao": "arquivar", "reasoning_tokens": 5}

    # _execute_action succeeds (archive is irreversible → _side_effects_executed = True)
    gmail.archive_email.return_value = None

    # Telegram notification blows up after the archive side effect
    telegram.send_email_notification.side_effect = RuntimeError("Telegram down")

    result = await proc.process_email("em1", "vip@corp.com")
    assert result["status"] == "error"
    # Claim must be kept (side effects already fired)
    db.release_claim.assert_not_called()

    # update_decision is called twice: once in step 7 (happy path), once in except block
    assert db.update_decision.call_count == 2

    # The SECOND call (except block) must have real subject/from from the parsed email
    partial_call = db.update_decision.call_args_list[1]
    data = partial_call[0][1]
    assert data["subject"] == "Contrato urgente", (
        f"Partial error finalization must preserve real subject, got: {data['subject']!r}"
    )
    assert data["from"] == "vip@corp.com", (
        f"Partial error finalization must preserve real sender, got: {data['from']!r}"
    )


@pytest.mark.asyncio
async def test_execute_action_mid_failure_keeps_claim():
    """If _execute_action raises mid-way for an irreversible action (e.g. archive
    API call succeeds at network level but method throws afterwards), the claim
    must NOT be released — the external effect already happened.

    The flag must be set BEFORE _execute_action is called so that even if the
    method raises, the except block knows side effects may have fired.
    """
    from orchestrator.handlers.email_processor import EmailProcessor
    db = AsyncMock()
    qdrant = MagicMock()
    qdrant.is_connected.return_value = False
    llm = AsyncMock()
    gmail = AsyncMock()
    telegram = AsyncMock()

    proc = EmailProcessor(db, qdrant, llm, gmail, telegram)

    gmail.get_email.return_value = {
        "id": "em1", "from": "a@b.com", "from_email": "a@b.com",
        "from_name": "A", "subject": "Arquivar isso", "body": "corpo",
        "body_clean": "", "attachments": [], "threadId": "t1", "date": "2026-04-14",
    }
    db.get_account.return_value = {"id": 1}
    db.claim_email.return_value = 66

    llm.classify_email.return_value = {
        "prioridade": "Alta", "importante": True, "confianca": 0.9,
        "categoria": "trabalho", "reasoning_tokens": 10,
    }
    llm.summarize_email.return_value = {"resumo": "Resumo", "reasoning_tokens": 5}
    llm.decide_action.return_value = {"acao": "arquivar", "reasoning_tokens": 5}

    # archive_email itself raises (simulates mid-execution failure)
    gmail.archive_email.side_effect = RuntimeError("Gmail API partial failure")

    result = await proc.process_email("em1", "a@b.com")
    assert result["status"] == "error"
    # Flag was set BEFORE the call → claim must NOT be released
    db.release_claim.assert_not_called()
    # Partial finalization must have been written
    assert db.update_decision.call_count == 2  # step 7 + except block
