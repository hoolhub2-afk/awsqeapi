#!/usr/bin/env python3
"""
Script to re-enable all accounts
Set enabled=1 for all accounts, preserving error and success counts
"""
import sys
import asyncio
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv

# Load .env file from parent directory
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.database import init_db, close_db


async def reset_all_accounts():
    """Re-enable all accounts (without resetting error and success counts)"""
    db = await init_db()

    try:
        # Get current disabled account count
        disabled_row = await db.fetchone("SELECT COUNT(*) as cnt FROM accounts WHERE enabled=0")
        disabled_count = disabled_row['cnt'] if disabled_row else 0

        # Get total account count
        total_row = await db.fetchone("SELECT COUNT(*) as cnt FROM accounts")
        total_count = total_row['cnt'] if total_row else 0

        print(f"Total accounts in database: {total_count}")
        print(f"Disabled accounts: {disabled_count}")

        if disabled_count == 0:
            print("All accounts are already enabled, no action needed")
            await close_db()
            return

        # Re-enable accounts only, without resetting error and success counts
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        await db.execute("""
            UPDATE accounts
            SET enabled=1, updated_at=?
            WHERE enabled=0
        """, (now,))

        print(f"[OK] Re-enabled {disabled_count} accounts")
        print("[OK] Preserved error and success counts for all accounts")

        # Show updated status
        rows = await db.fetchall("""
            SELECT id, label, enabled, error_count, success_count
            FROM accounts
            ORDER BY created_at DESC
        """)

        print("\nCurrent account status:")
        print("-" * 80)
        print(f"{'ID':<38} {'Label':<20} {'Enabled':<8} {'Errors':<8} {'Success':<8}")
        print("-" * 80)
        for row in rows:
            acc_id = row['id']
            label = row['label'] or "(no label)"
            enabled = row['enabled']
            error_count = row['error_count'] or 0
            success_count = row['success_count'] or 0
            enabled_str = "Yes" if enabled else "No"
            print(f"{acc_id:<38} {label:<20} {enabled_str:<8} {error_count:<8} {success_count:<8}")

    finally:
        await close_db()


async def main_async():
    print("=" * 80)
    print("Re-enable All Accounts")
    print("=" * 80)
    print()

    try:
        await reset_all_accounts()
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return 1

    print()
    print("=" * 80)
    print("Operation completed")
    print("=" * 80)
    return 0


def main():
    return asyncio.run(main_async())


if __name__ == "__main__":
    exit(main())
