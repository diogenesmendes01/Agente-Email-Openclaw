"""Tests for setup_steps/database.py."""
import pytest
from unittest.mock import patch, MagicMock, call


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
