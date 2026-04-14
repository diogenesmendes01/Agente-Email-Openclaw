#!/usr/bin/env python3
"""Register a Gmail account in the database.

Must be run once per account before the orchestrator can process emails.

Usage:
    python scripts/seed_account.py --email you@gmail.com \
        --hook-token-env GMAIL_HOOK_TOKEN_1 \
        [--topic-id 123]
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg
from dotenv import load_dotenv

load_dotenv()


async def main(email: str, hook_token_env: str, topic_id: int | None):
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO accounts (email, hook_token_env, telegram_topic_id)
               VALUES ($1, $2, $3)
               ON CONFLICT (email) DO UPDATE SET
                   hook_token_env = EXCLUDED.hook_token_env,
                   telegram_topic_id = COALESCE(EXCLUDED.telegram_topic_id, accounts.telegram_topic_id)
               RETURNING id, email""",
            email, hook_token_env, topic_id,
        )
        print(f"Account #{row['id']}: {row['email']} — ready")
    await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Register a Gmail account in the database")
    parser.add_argument("--email", required=True, help="Gmail address (e.g. you@gmail.com)")
    parser.add_argument("--hook-token-env", required=True, help="Env var name for the webhook token (e.g. GMAIL_HOOK_TOKEN_1)")
    parser.add_argument("--topic-id", type=int, default=None, help="Telegram topic ID for this account")
    args = parser.parse_args()
    asyncio.run(main(args.email, args.hook_token_env, args.topic_id))
