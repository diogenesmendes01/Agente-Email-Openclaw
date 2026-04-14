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
