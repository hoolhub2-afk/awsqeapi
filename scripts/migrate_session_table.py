#!/usr/bin/env python3
"""Migration script to manually create session_accounts table"""
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.database import init_db
from src.services.session_service import SessionService

async def main():
    print("Initializing database...")
    db = await init_db()

    print("Creating session_accounts table...")
    await SessionService.initialize_session_table()

    print("[OK] Migration completed!")

if __name__ == "__main__":
    asyncio.run(main())
