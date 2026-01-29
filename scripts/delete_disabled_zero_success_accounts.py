#!/usr/bin/env python3
import sys
import asyncio
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from parent directory
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.database import init_db, close_db


async def delete_disabled_accounts() -> int:
    """
    Delete accounts where enabled=0 AND success_count=0 from the database.
    Returns the number of rows deleted.
    """
    db = await init_db()

    try:
        # Count first for clear reporting
        count_row = await db.fetchone("SELECT COUNT(*) as cnt FROM accounts WHERE enabled=0 AND success_count=0")
        count = count_row['cnt'] if count_row else 0

        if count > 0:
            await db.execute("DELETE FROM accounts WHERE enabled=0 AND success_count=0")

        print(f"Deleted {count} disabled account(s) with zero success count.")
        return int(count)
    except Exception as e:
        print(f"Database error: {e}", file=sys.stderr)
        return 0
    finally:
        await close_db()


async def main_async() -> None:
    await delete_disabled_accounts()
    sys.exit(0)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
