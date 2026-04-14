"""Step: Gmail OAuth setup + Watch activation."""

import subprocess
import sys
import uuid
from pathlib import Path

from setup_steps.common import (
    step_header, ask, ask_choice, confirm, success, error, warning, spinner,
)


def check_client_secret(project_dir: Path) -> bool:
    """Check if credentials/client_secret.json exists."""
    return (project_dir / "credentials" / "client_secret.json").exists()


def count_existing_accounts(env: dict) -> int:
    """Count existing GMAIL_ACCOUNT_N entries in env (scans all 20 slots, tolerates gaps)."""
    max_slot = 0
    for i in range(1, 21):
        if env.get(f"GMAIL_ACCOUNT_{i}"):
            max_slot = i
    return max_slot


def add_account(project_dir: Path, env: dict, account_num: int) -> dict | None:
    """Add a single Gmail account. Returns account info dict or None."""
    email = ask("Email da conta Gmail")
    if not email:
        return None

    account_type_idx = ask_choice("Tipo de conta:", ["Pessoal (Gmail)", "Corporativa (Google Workspace)"])
    is_corporate = account_type_idx == 1

    if is_corporate:
        warning("Conta corporativa: o administrador do Google Workspace")
        warning("precisa aprovar o app OAuth antes da autenticação.")
        if not confirm("Continuar?"):
            return None

    # Run gmail_auth.py
    print()
    success(f"Autenticando {email}...")
    auth_result = subprocess.run(
        [sys.executable, str(project_dir / "scripts" / "gmail_auth.py"), "--account", email],
    )
    if auth_result.returncode != 0:
        error(f"Falha na autenticação OAuth para {email}")
        return None
    success(f"OAuth concluído para {email}")

    # Run gmail_watch.py
    pubsub_topic = env.get("GMAIL_PUBSUB_TOPIC", "")
    if pubsub_topic:
        print()
        with spinner(f"Ativando Gmail Watch para {email}..."):
            watch_result = subprocess.run(
                [sys.executable, str(project_dir / "scripts" / "gmail_watch.py"),
                 "--account", email, "--topic", pubsub_topic],
                capture_output=True, text=True,
            )
        if watch_result.returncode == 0:
            success(f"Gmail Watch ativado para {email}")
        else:
            warning(f"Gmail Watch falhou (pode ser configurado depois): {watch_result.stderr[:200]}")
    else:
        warning("GMAIL_PUBSUB_TOPIC não configurado — pulando Gmail Watch")

    # Generate hook token
    hook_token = uuid.uuid4().hex

    # Update env
    env[f"GMAIL_ACCOUNT_{account_num}"] = email
    env[f"GMAIL_HOOK_TOKEN_{account_num}"] = hook_token

    return {
        "email": email,
        "is_corporate": is_corporate,
        "account_num": account_num,
        "hook_token_env": f"GMAIL_HOOK_TOKEN_{account_num}",
    }


def run(project_dir: Path, env: dict) -> list[dict]:
    """Add Gmail accounts interactively. Returns list of account info dicts."""
    step_header(5, "Gmail")

    if not check_client_secret(project_dir):
        error("Arquivo credentials/client_secret.json não encontrado!")
        print()
        print("    Para criar:")
        print("      1. Acesse https://console.cloud.google.com/apis/credentials")
        print('      2. Crie credenciais OAuth 2.0 (tipo "Desktop App")')
        print("      3. Baixe o JSON e salve como credentials/client_secret.json")
        print()
        if not confirm("Tentar novamente após configurar?", default=False):
            warning("Passo Gmail pulado — configure manualmente depois")
            return []
        if not check_client_secret(project_dir):
            error("Arquivo ainda não encontrado — pulando Gmail")
            return []

    existing_count = count_existing_accounts(env)
    accounts = []
    account_num = existing_count + 1

    MAX_GMAIL_SLOTS = 20

    while account_num <= MAX_GMAIL_SLOTS:
        if not confirm(f"Adicionar conta Gmail #{account_num}?", default=account_num == 1):
            break

        account_info = add_account(project_dir, env, account_num)
        if account_info:
            accounts.append(account_info)
            account_num += 1
            success(f"Conta #{account_info['account_num']} configurada: {account_info['email']}")

    if account_num > MAX_GMAIL_SLOTS:
        warning(f"Limite de {MAX_GMAIL_SLOTS} contas Gmail atingido")

    if not accounts and existing_count == 0:
        warning("Nenhuma conta Gmail configurada")

    return accounts
