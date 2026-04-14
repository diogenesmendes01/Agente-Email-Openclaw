#!/usr/bin/env python3
"""Import playbooks from YAML file into PostgreSQL.

Usage: python scripts/import_playbooks.py playbooks/codewave.yaml --account-id 1
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
import asyncpg
from dotenv import load_dotenv
import os

load_dotenv()


async def main(yaml_path: str, account_id: int):
    with open(yaml_path) as f:
        data = yaml.safe_load(f)

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)

    async with pool.acquire() as conn:
        # Upsert company profile
        company_id = await conn.fetchval(
            """INSERT INTO company_profiles (account_id, company_name, cnpj, tone, signature, whatsapp_url)
               VALUES ($1, $2, $3, $4, $5, $6)
               ON CONFLICT (account_id) DO UPDATE SET
                   company_name = EXCLUDED.company_name,
                   cnpj = COALESCE(EXCLUDED.cnpj, company_profiles.cnpj),
                   tone = COALESCE(EXCLUDED.tone, company_profiles.tone),
                   signature = COALESCE(EXCLUDED.signature, company_profiles.signature),
                   whatsapp_url = COALESCE(EXCLUDED.whatsapp_url, company_profiles.whatsapp_url),
                   updated_at = NOW()
               RETURNING id""",
            account_id,
            data.get("empresa", "Unknown"),
            data.get("cnpj"),
            data.get("tom"),
            data.get("assinatura"),
            data.get("whatsapp_reembolso"),
        )
        print(f"Company profile #{company_id} upserted")

        # Import playbooks (idempotent — upsert on company_id + trigger_description)
        for i, pb in enumerate(data.get("playbooks", [])):
            pb_id = await conn.fetchval(
                """INSERT INTO playbooks (company_id, trigger_description, response_template, auto_respond, priority)
                   VALUES ($1, $2, $3, $4, $5)
                   ON CONFLICT (company_id, trigger_description) DO UPDATE SET
                       response_template = EXCLUDED.response_template,
                       auto_respond = EXCLUDED.auto_respond,
                       priority = EXCLUDED.priority,
                       updated_at = NOW()
                   RETURNING id""",
                company_id,
                pb["gatilho"],
                pb["template"],
                pb.get("auto", True),
                i,
            )
            print(f"  Playbook #{pb_id}: {pb['gatilho'][:40]}")

    await pool.close()
    print("Done!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import playbooks from YAML")
    parser.add_argument("yaml_file", help="Path to YAML file")
    parser.add_argument("--account-id", type=int, required=True, help="Account ID")
    args = parser.parse_args()
    asyncio.run(main(args.yaml_file, args.account_id))
