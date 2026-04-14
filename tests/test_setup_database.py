"""Tests for setup_steps/database.py."""
import os
import pytest
from unittest.mock import patch, MagicMock, call


def _pg_available():
    """Return True if a PostgreSQL test instance is reachable."""
    dsn = os.environ.get("TEST_DATABASE_URL", "")
    if not dsn:
        return False
    try:
        import psycopg2
        conn = psycopg2.connect(dsn)
        conn.close()
        return True
    except Exception:
        return False


requires_pg = pytest.mark.skipif(
    not _pg_available(),
    reason="TEST_DATABASE_URL not set or PostgreSQL not reachable",
)


class TestParseDatabaseUrl:
    def test_parses_standard_url(self):
        from setup_steps.database import parse_database_url
        parts = parse_database_url("postgresql://user:pass@localhost:5432/mydb")
        assert parts == {
            "user": "user", "password": "pass",
            "host": "localhost", "port": "5432", "dbname": "mydb",
        }

    def test_parses_encoded_password(self):
        from setup_steps.database import parse_database_url
        parts = parse_database_url("postgresql://user:p%40ss@host:5432/db")
        assert parts["password"] == "p@ss"


class TestCheckDatabaseExists:
    def test_returns_true_if_exists(self):
        from setup_steps.database import check_database_exists
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = (1,)
        assert check_database_exists(mock_conn, "mydb") is True

    def test_returns_false_if_not_exists(self):
        from setup_steps.database import check_database_exists
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = None
        assert check_database_exists(mock_conn, "mydb") is False


class TestRunSqlFile:
    def test_executes_sql_content(self):
        from setup_steps.database import run_sql_file
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        from pathlib import Path
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False) as f:
            f.write("CREATE TABLE IF NOT EXISTS test (id INT);")
            f.flush()
            run_sql_file(mock_conn, Path(f.name))
        mock_cursor.execute.assert_called_once()


class TestNoRedundantIndexes:
    """schema.sql constraint names must match migration 002 index names
    so that CREATE UNIQUE INDEX IF NOT EXISTS is a no-op on fresh installs."""

    def test_schema_constraint_names_match_migration_index_names(self):
        import re
        from pathlib import Path

        project_dir = Path(__file__).resolve().parent.parent
        schema_sql = (project_dir / "sql" / "schema.sql").read_text()
        migration_sql = (project_dir / "sql" / "migrations" / "002_idempotency_constraints.sql").read_text()

        # Extract named constraints from schema.sql: CONSTRAINT <name> UNIQUE(...)
        schema_constraints = set(re.findall(r"CONSTRAINT\s+(\w+)\s+UNIQUE", schema_sql, re.IGNORECASE))

        # Extract index names from migration 002: CREATE UNIQUE INDEX IF NOT EXISTS <name>
        # Filter to lines starting with CREATE (skip SQL comments)
        sql_lines = [l for l in migration_sql.splitlines() if not l.strip().startswith("--")]
        sql_body = "\n".join(sql_lines)
        migration_indexes = set(re.findall(r"CREATE\s+UNIQUE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+(\w+)", sql_body, re.IGNORECASE))

        # Every migration index must have a matching named constraint in schema.sql
        missing = migration_indexes - schema_constraints
        assert not missing, (
            f"Migration 002 creates indexes {missing} that don't match any named "
            f"constraint in schema.sql — fresh installs will get duplicate indexes. "
            f"Schema constraints: {schema_constraints}, Migration indexes: {migration_indexes}"
        )


class TestUpgradeExistingDatabase:
    """Simulate upgrading an existing database: schema + migrations run in order,
    and migration 002 deduplicates rows BEFORE creating unique indexes."""

    def test_schema_then_migrations_in_sorted_order(self):
        """run() must execute schema.sql first, then migrations in sorted filename order."""
        from setup_steps.database import run
        from pathlib import Path
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        sql_dir = tmp / "sql"
        sql_dir.mkdir()
        mig_dir = sql_dir / "migrations"
        mig_dir.mkdir()

        (sql_dir / "schema.sql").write_text("-- schema")
        (mig_dir / "001_phase3_4_tables.sql").write_text("-- mig 001")
        (mig_dir / "002_idempotency_constraints.sql").write_text("-- mig 002")

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        executed_sql = []
        original_execute = mock_cursor.execute
        mock_cursor.execute = lambda sql: executed_sql.append(sql)

        env = {"DATABASE_URL": "postgresql://user:pass@localhost:5432/mydb"}
        with patch("psycopg2.connect", return_value=mock_conn):
            run(tmp, env)

        assert len(executed_sql) == 3, f"Expected schema + 2 migrations, got {len(executed_sql)}"
        assert executed_sql[0] == "-- schema"
        assert executed_sql[1] == "-- mig 001"
        assert executed_sql[2] == "-- mig 002"

    def test_migration_002_deduplicates_before_creating_index(self):
        """In migration 002, DELETE (dedup) statements must precede
        CREATE UNIQUE INDEX statements, otherwise the index creation
        fails on a database that already has duplicate rows."""
        from pathlib import Path

        project_dir = Path(__file__).resolve().parent.parent
        migration = (project_dir / "sql" / "migrations" / "002_idempotency_constraints.sql").read_text()

        # Strip comments
        lines = [l for l in migration.splitlines() if not l.strip().startswith("--")]
        sql_body = "\n".join(lines)

        # For decisions: DELETE dedup must come before CREATE UNIQUE INDEX
        dedup_decisions_pos = sql_body.find("DELETE FROM decisions d1")
        index_decisions_pos = sql_body.find("idx_decisions_account_email")
        assert dedup_decisions_pos != -1, "Migration must deduplicate decisions"
        assert index_decisions_pos != -1, "Migration must create decisions unique index"
        assert dedup_decisions_pos < index_decisions_pos, (
            "decisions dedup DELETE must precede CREATE UNIQUE INDEX"
        )

        # For playbooks: DELETE dedup must come before CREATE UNIQUE INDEX
        dedup_playbooks_pos = sql_body.find("DELETE FROM playbooks p1")
        index_playbooks_pos = sql_body.find("idx_playbooks_company_trigger")
        assert dedup_playbooks_pos != -1, "Migration must deduplicate playbooks"
        assert index_playbooks_pos != -1, "Migration must create playbooks unique index"
        assert dedup_playbooks_pos < index_playbooks_pos, (
            "playbooks dedup DELETE must precede CREATE UNIQUE INDEX"
        )

        # NOT NULL: DELETE NULL rows must precede ALTER SET NOT NULL
        delete_null_pos = sql_body.find("DELETE FROM decisions WHERE account_id IS NULL")
        alter_not_null_pos = sql_body.find("ALTER TABLE decisions ALTER COLUMN account_id SET NOT NULL")
        assert delete_null_pos != -1, "Migration must clean NULL account_ids"
        assert alter_not_null_pos != -1, "Migration must set NOT NULL"
        assert delete_null_pos < alter_not_null_pos, (
            "DELETE NULL rows must precede ALTER SET NOT NULL"
        )


class TestRunDirectConnectionFirst:
    def test_connects_directly_to_target_db(self):
        """run() should try connecting directly to the target DB before falling back to admin."""
        from setup_steps.database import run
        from pathlib import Path
        import tempfile, os

        tmp = Path(tempfile.mkdtemp())
        sql_dir = tmp / "sql"
        sql_dir.mkdir()
        (sql_dir / "schema.sql").write_text("SELECT 1;")

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        env = {"DATABASE_URL": "postgresql://user:pass@localhost:5432/mydb"}
        with patch("psycopg2.connect", return_value=mock_conn) as mock_connect:
            run(tmp, env)
        # First call should be the direct connection to the target DB
        mock_connect.assert_any_call("postgresql://user:pass@localhost:5432/mydb")


@requires_pg
class TestRealPostgresUpgrade:
    """Run migration 002 against a real PostgreSQL instance with duplicate data.

    Requires TEST_DATABASE_URL env var pointing to a throwaway database.
    Skipped automatically when PostgreSQL is not available.
    """

    OLD_SCHEMA = """
    -- Minimal old schema WITHOUT idempotency constraints (pre-migration-002)
    DROP TABLE IF EXISTS playbooks CASCADE;
    DROP TABLE IF EXISTS company_profiles CASCADE;
    DROP TABLE IF EXISTS decisions CASCADE;
    DROP TABLE IF EXISTS accounts CASCADE;

    CREATE TABLE accounts (
        id SERIAL PRIMARY KEY,
        email VARCHAR(255) UNIQUE NOT NULL,
        hook_token_env VARCHAR(100) NOT NULL,
        oauth_token_path VARCHAR(255),
        telegram_topic_id BIGINT,
        learning_counter INT DEFAULT 0,
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

    CREATE TABLE company_profiles (
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

    CREATE TABLE playbooks (
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
    """

    @pytest.fixture(autouse=True)
    def _setup_teardown(self):
        """Create old schema, yield for test, then drop tables."""
        import psycopg2
        dsn = os.environ["TEST_DATABASE_URL"]
        self.conn = psycopg2.connect(dsn)
        self.conn.autocommit = True
        with self.conn.cursor() as cur:
            cur.execute(self.OLD_SCHEMA)
        yield
        with self.conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS playbooks, company_profiles, decisions, accounts CASCADE")
        self.conn.close()

    def _seed_duplicates(self):
        """Insert an account, then duplicate rows in decisions and playbooks."""
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO accounts (email, hook_token_env) VALUES ('a@g.com', 'T') RETURNING id"
            )
            account_id = cur.fetchone()[0]

            # A NULL account_id row (orphan) + 2 duplicate decision rows
            cur.execute(
                "INSERT INTO decisions (account_id, email_id, subject) VALUES (NULL, 'eid1', 'orphan')"
            )
            cur.execute(
                "INSERT INTO decisions (account_id, email_id, subject) VALUES (%s, 'eid2', 'first')",
                (account_id,),
            )
            cur.execute(
                "INSERT INTO decisions (account_id, email_id, subject) VALUES (%s, 'eid2', 'duplicate')",
                (account_id,),
            )

            # Company profile + 2 duplicate playbook rows
            cur.execute(
                "INSERT INTO company_profiles (account_id, company_name) VALUES (%s, 'Acme') RETURNING id",
                (account_id,),
            )
            company_id = cur.fetchone()[0]

            cur.execute(
                "INSERT INTO playbooks (company_id, trigger_description, response_template) "
                "VALUES (%s, 'trigger1', 'tmpl-first')", (company_id,),
            )
            cur.execute(
                "INSERT INTO playbooks (company_id, trigger_description, response_template) "
                "VALUES (%s, 'trigger1', 'tmpl-dup')", (company_id,),
            )
            return account_id, company_id

    def _run_migration(self):
        from pathlib import Path
        project_dir = Path(__file__).resolve().parent.parent
        migration_sql = (project_dir / "sql" / "migrations" / "002_idempotency_constraints.sql").read_text()
        with self.conn.cursor() as cur:
            cur.execute(migration_sql)

    def test_migration_deduplicates_decisions(self):
        """Duplicate decisions rows are reduced to one; NULL account_id rows are deleted."""
        account_id, _ = self._seed_duplicates()

        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM decisions")
            assert cur.fetchone()[0] == 3  # 1 orphan + 2 dupes

        self._run_migration()

        with self.conn.cursor() as cur:
            # Orphan (NULL account_id) deleted
            cur.execute("SELECT COUNT(*) FROM decisions WHERE account_id IS NULL")
            assert cur.fetchone()[0] == 0

            # Duplicate pair reduced to 1 (the one with higher id survives)
            cur.execute("SELECT COUNT(*) FROM decisions WHERE account_id = %s AND email_id = 'eid2'", (account_id,))
            assert cur.fetchone()[0] == 1

            cur.execute(
                "SELECT subject FROM decisions WHERE account_id = %s AND email_id = 'eid2'", (account_id,),
            )
            assert cur.fetchone()[0] == "duplicate"  # higher id kept

    def test_migration_deduplicates_playbooks(self):
        """Duplicate playbooks rows are reduced to one (higher id kept)."""
        _, company_id = self._seed_duplicates()

        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM playbooks WHERE company_id = %s", (company_id,))
            assert cur.fetchone()[0] == 2

        self._run_migration()

        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM playbooks WHERE company_id = %s", (company_id,))
            assert cur.fetchone()[0] == 1

            cur.execute(
                "SELECT response_template FROM playbooks WHERE company_id = %s AND trigger_description = 'trigger1'",
                (company_id,),
            )
            assert cur.fetchone()[0] == "tmpl-dup"  # higher id kept

    def test_unique_constraints_enforced_after_migration(self):
        """After migration, inserting a duplicate must raise an integrity error."""
        import psycopg2
        account_id, company_id = self._seed_duplicates()
        self._run_migration()

        # decisions: duplicate (account_id, email_id) must fail
        with self.conn.cursor() as cur:
            with pytest.raises(psycopg2.errors.UniqueViolation):
                cur.execute(
                    "INSERT INTO decisions (account_id, email_id, subject) VALUES (%s, 'eid2', 'boom')",
                    (account_id,),
                )
        # Reset after the error
        self.conn.rollback()

        # playbooks: duplicate (company_id, trigger_description) must fail
        with self.conn.cursor() as cur:
            with pytest.raises(psycopg2.errors.UniqueViolation):
                cur.execute(
                    "INSERT INTO playbooks (company_id, trigger_description, response_template) "
                    "VALUES (%s, 'trigger1', 'boom')", (company_id,),
                )
        self.conn.rollback()

    def test_not_null_enforced_after_migration(self):
        """After migration, inserting a decision with NULL account_id must fail."""
        import psycopg2
        self._seed_duplicates()
        self._run_migration()

        with self.conn.cursor() as cur:
            with pytest.raises(psycopg2.errors.NotNullViolation):
                cur.execute(
                    "INSERT INTO decisions (account_id, email_id, subject) VALUES (NULL, 'x', 'boom')"
                )
        self.conn.rollback()
