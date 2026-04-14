#!/usr/bin/env python3
"""
Gmail OAuth Setup - Autenticacao unica para a Gmail API

Uso:
    python scripts/gmail_auth.py --account seu@email.com

Pre-requisitos:
    1. Crie um projeto no Google Cloud Console
    2. Ative a Gmail API
    3. Crie credenciais OAuth 2.0 (tipo "Desktop App")
    4. Baixe o client_secret.json e coloque em credentials/client_secret.json

O script abre o navegador para autorizar o acesso e salva o token em:
    credentials/token_seu@email.com.json
"""

import os
import sys
import argparse
from pathlib import Path

# Adicionar raiz do projeto ao path
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.readonly",
]

CREDENTIALS_DIR = PROJECT_DIR / "credentials"


def authenticate(account: str):
    """Executa fluxo OAuth e salva token"""
    CREDENTIALS_DIR.mkdir(exist_ok=True)

    client_secret = CREDENTIALS_DIR / "client_secret.json"
    if not client_secret.exists():
        print(f"ERRO: Arquivo {client_secret} nao encontrado!")
        print()
        print("Para criar:")
        print("  1. Acesse https://console.cloud.google.com/apis/credentials")
        print("  2. Crie credenciais OAuth 2.0 (tipo 'Desktop App')")
        print("  3. Baixe o JSON e salve como credentials/client_secret.json")
        sys.exit(1)

    token_file = CREDENTIALS_DIR / f"token_{account}.json"

    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if creds and creds.valid:
        print(f"Token valido para {account}")
        return

    if creds and creds.expired and creds.refresh_token:
        print(f"Renovando token para {account}...")
        creds.refresh(Request())
    else:
        print(f"Iniciando autorizacao OAuth para {account}...")
        print(f"IMPORTANTE: Faca login com a conta {account}")
        print()

        flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)

        # Try local server first (works on machines with a browser).
        # Fall back to manual copy-paste flow for headless servers (VPS).
        try:
            creds = flow.run_local_server(port=0)
        except Exception:
            print("Navegador nao disponivel — usando modo manual.")
            print()
            auth_url, _ = flow.authorization_url(prompt="consent")
            print("Abra este link no navegador do seu PC:")
            print()
            print(f"  {auth_url}")
            print()
            code = input("Cole o codigo de autorizacao aqui: ").strip()
            flow.fetch_token(code=code)
            creds = flow.credentials

    # Salvar token
    with open(token_file, "w") as f:
        f.write(creds.to_json())

    print(f"Token salvo em: {token_file}")
    print(f"Conta {account} autenticada com sucesso!")


def main():
    parser = argparse.ArgumentParser(description="Autenticar conta Gmail para o Email Agent")
    parser.add_argument("--account", required=True, help="Email da conta Gmail (ex: seu@gmail.com)")
    args = parser.parse_args()

    authenticate(args.account)


if __name__ == "__main__":
    main()
