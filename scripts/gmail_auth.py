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
        print("O navegador vai abrir para voce autorizar o acesso.")
        print(f"IMPORTANTE: Faca login com a conta {account}")
        print()

        flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
        creds = flow.run_local_server(port=0)

    # Validate that the authenticated account matches --account
    try:
        from googleapiclient.discovery import build
        service = build("gmail", "v1", credentials=creds)
        profile = service.users().getProfile(userId="me").execute()
        authenticated_email = profile.get("emailAddress", "").lower()
        if authenticated_email != account.lower():
            print(f"\nERRO: Voce autenticou com '{authenticated_email}' mas --account e '{account}'")
            print("O token NAO foi salvo. Tente novamente com a conta correta.")
            sys.exit(1)
    except Exception as e:
        print(f"\nAVISO: Nao foi possivel verificar a conta autenticada: {e}")
        print("Verifique manualmente se o login foi feito com a conta correta.")

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
