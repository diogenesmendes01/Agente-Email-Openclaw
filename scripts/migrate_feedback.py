#!/usr/bin/env python3
"""
One-time migration: reads feedback.json and backfills structured feedback data into Qdrant.

Usage: python scripts/migrate_feedback.py
"""

import os
import sys
import json
import asyncio
from pathlib import Path

# Setup paths
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

from orchestrator.services.qdrant_service import QdrantService


async def migrate():
    feedback_file = BASE_DIR / "feedback.json"
    if not feedback_file.exists():
        print("feedback.json not found, nothing to migrate.")
        return

    with open(feedback_file) as f:
        feedback_data = json.load(f)

    if not feedback_data:
        print("feedback.json is empty.")
        return

    qdrant = QdrantService()
    if not qdrant.is_connected():
        print("ERROR: Qdrant not connected. Start Qdrant and try again.")
        sys.exit(1)

    migrated = 0
    errors = 0
    for entry in feedback_data:
        email_id = entry.get("email_id", "")
        if not email_id:
            continue

        try:
            success = await qdrant.update_feedback(
                email_id=email_id,
                feedback="corrected",
                original_priority=entry.get("original_urgency", ""),
                corrected_priority=entry.get("corrected_urgency", ""),
            )
            if success:
                migrated += 1
            else:
                errors += 1
        except Exception as e:
            print(f"Error migrating {email_id}: {e}")
            errors += 1

    print(f"Migration complete: {migrated} migrated, {errors} skipped/errors out of {len(feedback_data)} total.")


if __name__ == "__main__":
    asyncio.run(migrate())
