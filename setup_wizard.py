#!/usr/bin/env python3
"""
Agente Email — Setup Wizard Interativo

Uso:
    python setup_wizard.py

Guia o desenvolvedor pela configuração completa do sistema.
"""

import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent


def ensure_bootstrap_deps():
    """Install rich and python-dotenv before anything else."""
    # Map: pip package name → actual Python module name
    bootstrap_pkgs = {"rich": "rich", "python-dotenv": "dotenv"}
    for pip_name, module_name in bootstrap_pkgs.items():
        try:
            __import__(module_name)
        except ImportError:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", pip_name, "-q"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )


def ensure_requirements():
    """Check that critical packages are available, install if not."""
    try:
        import psycopg2  # noqa: F401
        import requests  # noqa: F401
    except ImportError:
        from setup_steps.common import warning
        warning("Dependências faltando — instalando requirements.txt...")
        from setup_steps.dependencies import run as run_deps
        run_deps(PROJECT_DIR)


def detect_state() -> dict:
    """Detect what's already configured."""
    state = {
        "env_exists": (PROJECT_DIR / ".env").exists(),
        "credentials_exist": (PROJECT_DIR / "credentials" / "client_secret.json").exists(),
    }
    return state


def first_run():
    """Execute all steps sequentially for a fresh install."""
    from setup_steps import dependencies, env_config, database, telegram, gmail, accounts, playbooks
    from setup_steps.common import success, error, warning, confirm

    warnings = []

    # Step 1: Dependencies
    if not dependencies.run(PROJECT_DIR):
        if not confirm("Continuar mesmo assim?", default=False):
            return

    # Step 2: Environment variables
    env = env_config.run(PROJECT_DIR)

    # Step 3: Database
    if not database.run(PROJECT_DIR, env):
        warnings.append("PostgreSQL")
        if not confirm("Continuar mesmo assim?", default=False):
            return

    # Step 4: Telegram
    if not telegram.run(env):
        warnings.append("Telegram")

    # Update .env with any changes from telegram step (chat_id discovery)
    env_config.write_env_file(PROJECT_DIR / ".env", env)

    # Step 5: Gmail
    gmail_accounts = gmail.run(PROJECT_DIR, env)
    if not gmail_accounts:
        warnings.append("Gmail (nenhuma conta configurada)")

    # Update .env with gmail accounts
    env_config.write_env_file(PROJECT_DIR / ".env", env)

    # Step 6: Accounts in DB
    gmail_accounts = accounts.run(PROJECT_DIR, env, gmail_accounts)
    if gmail_accounts and not all(a.get("account_id") for a in gmail_accounts):
        warnings.append("Contas no banco (algumas não foram criadas)")

    # Step 7: Playbooks
    if not playbooks.run(PROJECT_DIR, gmail_accounts):
        warnings.append("Playbooks")

    # Summary
    print()
    if warnings:
        warning("=" * 40)
        warning("Setup concluído com pendências:")
        for w in warnings:
            warning(f"  • {w}")
        warning("=" * 40)
        print()
        print("    Corrija os itens acima e execute novamente:")
        print("      python setup_wizard.py")
    else:
        success("=" * 40)
        success("Setup concluído!")
        success("=" * 40)
        print()
        print("    Para iniciar o sistema:")
        print("      python -m uvicorn orchestrator.main:app --host 0.0.0.0 --port 8787")
    print()


def _load_corporate_accounts_from_db(env: dict) -> list[dict]:
    """Load accounts with company profiles from the database for re-run scenarios."""
    try:
        import psycopg2
        conn = psycopg2.connect(env.get("DATABASE_URL", ""))
        with conn.cursor() as cur:
            cur.execute(
                """SELECT a.id, a.email, cp.id as company_id
                   FROM accounts a
                   JOIN company_profiles cp ON cp.account_id = a.id"""
            )
            rows = cur.fetchall()
        conn.close()
        return [
            {"email": r[1], "account_id": r[0], "company_id": r[2], "is_corporate": True}
            for r in rows
        ]
    except Exception:
        return []


def rerun_menu():
    """Show re-execution menu and handle choice."""
    from setup_steps.common import ask_choice, banner
    from setup_steps import env_config, database, telegram, gmail, accounts, playbooks

    choices = [
        "Reconfigurar tudo do zero",
        "Reconfigurar variáveis de ambiente (.env)",
        "Recriar/atualizar banco de dados",
        "Adicionar nova conta Gmail",
        "Reconfigurar Telegram",
        "Reimportar playbooks",
        "Validar instalação",
        "Sair",
    ]

    idx = ask_choice("O que deseja fazer?", choices)

    if idx == 7:  # Sair
        return

    ensure_requirements()

    existing_env = env_config.parse_existing_env(PROJECT_DIR / ".env")

    if idx == 0:  # Tudo do zero
        first_run()

    elif idx == 1:  # .env
        env = env_config.run(PROJECT_DIR, existing=existing_env)

    elif idx == 2:  # Database
        database.run(PROJECT_DIR, existing_env)

    elif idx == 3:  # Add Gmail
        gmail_accounts = gmail.run(PROJECT_DIR, existing_env)
        if gmail_accounts:
            env_config.write_env_file(PROJECT_DIR / ".env", existing_env)
            try:
                gmail_accounts = accounts.run(PROJECT_DIR, existing_env, gmail_accounts)
            except Exception as e:
                from setup_steps.common import error, warning
                error(f"Falha ao criar contas no banco: {e}")
                warning("As contas Gmail foram salvas no .env — crie as entradas no banco manualmente ou reexecute o wizard.")

    elif idx == 4:  # Telegram
        telegram.run(existing_env)
        env_config.write_env_file(PROJECT_DIR / ".env", existing_env)

    elif idx == 5:  # Playbooks
        corporate_accounts = _load_corporate_accounts_from_db(existing_env)
        playbooks.run(PROJECT_DIR, corporate_accounts)

    elif idx == 6:  # Validate
        run_validation(existing_env)


def run_validation(env: dict):
    """Run all validation checks without modifying anything."""
    from setup_steps.common import step_header, success, error, warning, spinner

    step_header(0, "Validação da Instalação")

    # PostgreSQL
    try:
        import psycopg2
        with spinner("Testando PostgreSQL..."):
            conn = psycopg2.connect(env.get("DATABASE_URL", ""))
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
                count = cur.fetchone()[0]
            conn.close()
        success(f"PostgreSQL — {count} tabelas encontradas")
    except Exception as e:
        error(f"PostgreSQL — {e}")

    # Telegram
    try:
        import requests
        with spinner("Testando Telegram..."):
            resp = requests.get(
                f"https://api.telegram.org/bot{env.get('TELEGRAM_BOT_TOKEN', '')}/getMe",
                timeout=10,
            )
            data = resp.json()
        if data.get("ok"):
            success(f"Telegram — @{data['result']['username']}")
        else:
            error("Telegram — token inválido")
    except Exception as e:
        error(f"Telegram — {e}")

    # Gmail tokens + Watch (scan all 20 slots, tolerate gaps)
    import time
    creds_dir = PROJECT_DIR / "credentials"
    gmail_count = 0
    for i in range(1, 21):
        email = env.get(f"GMAIL_ACCOUNT_{i}", "")
        if not email:
            continue
        token_file = creds_dir / f"token_{email}.json"
        if token_file.exists():
            success(f"Gmail — {email} (token encontrado)")
            gmail_count += 1
            # Watch age heuristic
            age_days = (time.time() - token_file.stat().st_mtime) / 86400
            if age_days > 7:
                warning(f"Gmail Watch — {email} token com {int(age_days)} dias (watch expira a cada 7)")
            else:
                success(f"Gmail Watch — {email} token recente ({int(age_days)}d)")
        else:
            error(f"Gmail — {email} (token NÃO encontrado)")
    if gmail_count == 0:
        error("Gmail — nenhuma conta configurada")

    # Qdrant
    try:
        import requests
        host = env.get("QDRANT_HOST", "localhost")
        port = env.get("QDRANT_PORT", "6333")
        with spinner("Testando Qdrant..."):
            resp = requests.get(f"http://{host}:{port}/collections", timeout=5)
        if resp.status_code == 200:
            success(f"Qdrant — acessível em {host}:{port}")
        else:
            error(f"Qdrant — status {resp.status_code}")
    except Exception as e:
        error(f"Qdrant — {e}")


def main():
    ensure_bootstrap_deps()

    from setup_steps.common import banner, warning

    banner()

    state = detect_state()

    if state["env_exists"]:
        print()
        warning("Instalação anterior detectada.")
        print()
        rerun_menu()
    else:
        first_run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Setup cancelado. Progresso parcial foi salvo.\n")
        sys.exit(1)
