"""Step: connect to PostgreSQL, create database, run schema + migrations."""

from pathlib import Path
from urllib.parse import urlparse, unquote

from setup_steps.common import step_header, success, error, warning, confirm, spinner


def parse_database_url(url: str) -> dict:
    """Parse DATABASE_URL into components."""
    parsed = urlparse(url)
    return {
        "user": parsed.username or "",
        "password": unquote(parsed.password or ""),
        "host": parsed.hostname or "localhost",
        "port": str(parsed.port or 5432),
        "dbname": parsed.path.lstrip("/"),
    }


def check_database_exists(conn, dbname: str) -> bool:
    """Check if a database exists by querying pg_database."""
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
        return cur.fetchone() is not None


def run_sql_file(conn, sql_path: Path):
    """Execute a SQL file against a connection."""
    sql = sql_path.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()


def run(project_dir: Path, env: dict) -> bool:
    """Create database if needed, run schema and migrations."""
    import psycopg2

    step_header(3, "PostgreSQL")

    database_url = env.get("DATABASE_URL", "")
    if not database_url:
        error("DATABASE_URL não encontrada no .env")
        return False

    parts = parse_database_url(database_url)
    target_db = parts["dbname"]

    # 1. Try connecting directly to the target database first
    conn = None
    try:
        with spinner(f"Conectando ao banco '{target_db}'..."):
            conn = psycopg2.connect(database_url)
            conn.autocommit = True
        success(f"Conectado ao banco '{target_db}' em {parts['host']}:{parts['port']}")
    except psycopg2.OperationalError:
        # Target DB may not exist yet — try creating it via admin connection
        warning(f"Banco '{target_db}' não acessível — tentando criar...")
        try:
            with spinner("Conectando ao PostgreSQL (admin)..."):
                admin_conn = psycopg2.connect(
                    host=parts["host"], port=parts["port"],
                    user=parts["user"], password=parts["password"],
                    dbname="postgres",
                )
                admin_conn.autocommit = True
        except Exception as e:
            error(f"Falha ao conectar ao PostgreSQL: {e}")
            error("Dica: verifique se o usuário tem acesso ao banco 'postgres' ou se o banco alvo já existe.")
            return False

        try:
            if not check_database_exists(admin_conn, target_db):
                with spinner(f"Criando banco '{target_db}'..."):
                    with admin_conn.cursor() as cur:
                        cur.execute(f'CREATE DATABASE "{target_db}"')
                success(f"Banco '{target_db}' criado")
            else:
                success(f"Banco '{target_db}' já existe")
        except Exception as e:
            error(f"Falha ao criar banco: {e}")
            return False
        finally:
            admin_conn.close()

        # Now connect to the newly created target DB
        try:
            conn = psycopg2.connect(database_url)
            conn.autocommit = True
        except Exception as e:
            error(f"Falha ao conectar ao banco '{target_db}': {e}")
            return False

    try:
        schema_file = project_dir / "sql" / "schema.sql"
        if schema_file.exists():
            with spinner("Criando tabelas (schema.sql)..."):
                run_sql_file(conn, schema_file)
            success("Tabelas criadas (schema.sql)")
        else:
            warning("sql/schema.sql não encontrado — pulando")

        migration_file = project_dir / "sql" / "migrations" / "001_phase3_4_tables.sql"
        if migration_file.exists():
            with spinner("Rodando migrations..."):
                run_sql_file(conn, migration_file)
            success("Migrations aplicadas (001_phase3_4_tables.sql)")
        else:
            warning("Migration 001 não encontrada — pulando")

        return True
    except Exception as e:
        error(f"Falha ao executar SQL: {e}")
        return False
    finally:
        conn.close()
