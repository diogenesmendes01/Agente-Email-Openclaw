#!/usr/bin/env python3
"""
Gmail Watch - Ativa/renova o Gmail Watch (Pub/Sub push notifications)

Uso:
    python scripts/gmail_watch.py --account seu@email.com --topic projects/SEU_PROJETO/topics/gmail-watch

O Gmail Watch expira a cada 7 dias. Configure um cron para renovar:
    0 0 */6 * * cd /path/to/project && python scripts/gmail_watch.py --account seu@email.com --topic projects/SEU_PROJETO/topics/gmail-watch
"""

import os
import sys
import asyncio
import argparse
from pathlib import Path

# Adicionar raiz do projeto ao path
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from dotenv import load_dotenv
load_dotenv(PROJECT_DIR / ".env")

from orchestrator.services.gmail_service import GmailService


async def activate_watch(account: str, topic: str):
    gmail = GmailService()

    if not gmail.is_ready():
        print("ERRO: Nenhuma conta Gmail autenticada.")
        print("Execute: python scripts/gmail_auth.py --account seu@email.com")
        sys.exit(1)

    result = await gmail.watch(account, topic)
    if result:
        print(f"Gmail Watch ativado para {account}")
        print(f"  History ID: {result.get('historyId')}")
        print(f"  Expira em: {result.get('expiration')} (ms desde epoch)")
    else:
        print(f"ERRO: Falha ao ativar Gmail Watch para {account}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Ativar/renovar Gmail Watch")
    parser.add_argument("--account", required=True, help="Email da conta Gmail")
    parser.add_argument("--topic", required=True, help="Topic do Pub/Sub (ex: projects/meu-projeto/topics/gmail-watch)")
    args = parser.parse_args()

    asyncio.run(activate_watch(args.account, args.topic))


if __name__ == "__main__":
    main()
