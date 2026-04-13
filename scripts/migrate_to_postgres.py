#!/usr/bin/env python3
"""Migrate VIP, blacklist, feedback, and history_ids data from JSON files to PostgreSQL."""

import os
import sys
import json
import asyncio
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from dotenv import load_dotenv
load_dotenv(PROJECT_DIR / ".env")

import asyncpg


async def migrate():
    dsn = os.environ["DATABASE_URL"]
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=3)

    async with pool.acquire() as conn:
        # Create accounts from env
        i = 1
        accounts = {}
        while True:
            email = os.getenv(f"GMAIL_ACCOUNT_{i}", "").strip()
            token_env = os.getenv(f"GMAIL_HOOK_TOKEN_{i}", "").strip()
            if not email:
                break
            row = await conn.fetchrow(
                """INSERT INTO accounts (email, hook_token_env, oauth_token_path)
                   VALUES ($1, $2, $3)
                   ON CONFLICT (email) DO UPDATE SET hook_token_env = $2
                   RETURNING id""",
                email, f"GMAIL_HOOK_TOKEN_{i}", f"credentials/token_{email}.json",
            )
            accounts[email] = row["id"]
            print(f"  Account: {email} -> id={row['id']}")
            i += 1

        if not accounts:
            print("ERROR: No GMAIL_ACCOUNT_N found in .env")
            return

        default_account_id = list(accounts.values())[0]

        # Migrate VIP list
        vip_file = PROJECT_DIR / "vip-list.json"
        if vip_file.exists():
            vips = json.loads(vip_file.read_text(encoding="utf-8"))
            count = 0
            for entry in vips:
                acct_email = entry.get("account", "")
                acct_id = accounts.get(acct_email, default_account_id)
                await conn.execute(
                    """INSERT INTO vip_list (account_id, sender_email, sender_name, min_urgency)
                       VALUES ($1, $2, $3, $4)
                       ON CONFLICT DO NOTHING""",
                    acct_id, entry["email"], entry.get("name", ""),
                    entry.get("min_urgency", "high"),
                )
                count += 1
            print(f"  VIPs migrated: {count}")

        # Migrate blacklist
        bl_file = PROJECT_DIR / "blacklist.json"
        if bl_file.exists():
            blacklist = json.loads(bl_file.read_text(encoding="utf-8"))
            count = 0
            for entry in blacklist:
                acct_email = entry.get("account", "")
                acct_id = accounts.get(acct_email, default_account_id)
                await conn.execute(
                    """INSERT INTO blacklist (account_id, sender_email, reason)
                       VALUES ($1, $2, $3)
                       ON CONFLICT DO NOTHING""",
                    acct_id, entry["email"], entry.get("reason", ""),
                )
                count += 1
            print(f"  Blacklist migrated: {count}")

        # Migrate feedback
        fb_file = PROJECT_DIR / "feedback.json"
        if fb_file.exists():
            feedback = json.loads(fb_file.read_text(encoding="utf-8"))
            count = 0
            for entry in feedback:
                acct_email = entry.get("account", "")
                acct_id = accounts.get(acct_email, default_account_id)
                await conn.execute(
                    """INSERT INTO feedback (account_id, email_id, sender, original_urgency, corrected_urgency, keywords)
                       VALUES ($1, $2, $3, $4, $5, $6)""",
                    acct_id,
                    entry.get("email_id", ""),
                    entry.get("from", ""),
                    entry.get("original_urgency", ""),
                    entry.get("corrected_urgency", ""),
                    entry.get("keywords", []),
                )
                count += 1
            print(f"  Feedback migrated: {count}")

        # Migrate history IDs
        hid_file = PROJECT_DIR / "history_ids.json"
        if hid_file.exists():
            history_ids = json.loads(hid_file.read_text(encoding="utf-8"))
            count = 0
            for email_key, hid_value in history_ids.items():
                acct_id = accounts.get(email_key)
                if acct_id and hid_value:
                    await conn.execute(
                        """INSERT INTO history_ids (account_id, history_id)
                           VALUES ($1, $2)
                           ON CONFLICT (account_id)
                           DO UPDATE SET history_id = $2, updated_at = NOW()""",
                        acct_id, str(hid_value),
                    )
                    count += 1
            print(f"  History IDs migrated: {count}")

    await pool.close()
    print("\nMigration complete!")


if __name__ == "__main__":
    print("Migrating to PostgreSQL...")
    asyncio.run(migrate())
