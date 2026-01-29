#!/usr/bin/env python3
"""
Print account statistics from the database.
"""

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

sys.path.insert(0, str(BASE_DIR))

from src.core.database import init_db, close_db, row_to_dict  # noqa: E402


async def gather_stats():
    db = await init_db()

    try:
        accounts = await db.fetchall("SELECT * FROM accounts ORDER BY created_at DESC")
    except Exception as e:
        print(f"Error querying accounts: {e}", file=sys.stderr)
        await close_db()
        sys.exit(1)

    accounts = [row_to_dict(acc) for acc in accounts]
    total_accounts = len(accounts)

    if total_accounts == 0:
        print("No accounts found in database.")
        await close_db()
        return

    enabled_accounts = [acc for acc in accounts if acc.get("enabled")]
    disabled_accounts = [acc for acc in accounts if not acc.get("enabled")]
    refresh_failed_accounts = [
        acc for acc in accounts if acc.get("last_refresh_status") == "failed"
    ]
    never_used_accounts = [acc for acc in accounts if acc.get("success_count", 0) == 0]
    error_accounts = [acc for acc in accounts if acc.get("error_count", 0) > 0]
    total_success_count = sum(acc.get("success_count", 0) for acc in accounts)

    print("--- Account Summary ---")
    print(f"Total accounts: {total_accounts}")
    print(f"  Enabled: {len(enabled_accounts)}")
    print(f"  Disabled: {len(disabled_accounts)}")
    print("-" * 24)
    print(f"Token refresh failed: {len(refresh_failed_accounts)}")
    print(f"Never used: {len(never_used_accounts)}")
    print(f"With errors: {len(error_accounts)}")
    print(f"Total success count: {total_success_count}")
    print("-" * 24)

    print("\n--- Account Details ---")
    header = "| {status:<8s} | {enabled:<6s} | {label:<15s} | {succ:<6s} | {err:<6s} | {refresh:<14s} | {refreshtime:<20s} |".format(
        status="Status",
        enabled="Enabled",
        label="Label",
        succ="Success",
        err="Errors",
        refresh="Last refresh",
        refreshtime="Updated at",
    )
    print(header)
    print("-" * len(header))

    for acc in accounts:
        status_icon = "ok" if acc.get("enabled") else "off"
        if acc.get("last_refresh_status") == "failed":
            status_icon = "warn"

        enabled_str = "yes" if acc.get("enabled") else "no"
        label = acc.get("label") or "(none)"
        if len(label) > 15:
            label = label[:12] + "..."

        last_refresh_time = acc.get("last_refresh_time") or "never"
        last_refresh_status = acc.get("last_refresh_status") or "never"

        print(
            "| {status:<10s} | {enabled:<6s} | {label:<15s} | {succ:<6d} | {err:<6d} | {refresh:<14s} | {refreshtime:<20s} |".format(
                status=status_icon,
                enabled=enabled_str,
                label=label,
                succ=acc.get("success_count", 0),
                err=acc.get("error_count", 0),
                refresh=last_refresh_status,
                refreshtime=last_refresh_time,
            )
        )

    print("-" * len(header))
    await close_db()


def main():
    asyncio.run(gather_stats())
    sys.exit(0)


if __name__ == "__main__":
    main()
