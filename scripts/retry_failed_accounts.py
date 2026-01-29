#!/usr/bin/env python3
import os
import sys
import time
import uuid
import asyncio
import traceback
from pathlib import Path
from typing import Dict, Any, Optional

import httpx
from dotenv import load_dotenv

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.database import init_db, close_db, row_to_dict

# --- Configuration ---
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
# --- Configuration End ---


# --- Core functions移植 from app.py ---

OIDC_BASE = "https://oidc.us-east-1.amazonaws.com"
TOKEN_URL = f"{OIDC_BASE}/token"

def _get_proxies() -> Optional[Dict[str, str]]:
    """Read HTTP proxy settings from environment variables."""
    proxy = os.getenv("HTTP_PROXY", "").strip()
    if proxy:
        return {"http": proxy, "https": proxy}
    return None

def _oidc_headers() -> Dict[str, str]:
    """Construct HTTP headers for OIDC requests."""
    return {
        "content-type": "application/json",
        "user-agent": "aws-sdk-rust/1.3.9 os/windows lang/rust/1.87.0",
        "x-amz-user-agent": "aws-sdk-rust/1.3.9 ua/2.1 api/ssooidc/1.88.0 os/windows lang/rust/1.87.0 m/E app/AmazonQ-For-CLI",
        "amz-sdk-request": "attempt=1; max=3",
        "amz-sdk-invocation-id": str(uuid.uuid4()),
    }

async def refresh_single_account_token(
    db,
    account: Dict[str, Any],
    client: httpx.AsyncClient
) -> bool:
    """
    Attempt to refresh accessToken for a single account.
    Returns True if successful (updates DB), False if failed (updates failure status).
    """
    account_id = account["id"]
    label = account.get("label") or account_id[:8]

    if not all(k in account for k in ["clientId", "clientSecret", "refreshToken"]):
        print(f"  [!] Account {label} missing required credentials, skipping.")
        return False

    payload = {
        "grantType": "refresh_token",
        "clientId": account["clientId"],
        "clientSecret": account["clientSecret"],
        "refreshToken": account["refreshToken"],
    }

    def _calc_expires_at(expires_in: Any) -> Optional[str]:
        if expires_in is None:
            return None
        try:
            seconds = int(expires_in)
        except Exception:
            return None
        if seconds <= 0:
            return None
        return time.strftime(
            "%Y-%m-%dT%H:%M:%S",
            time.gmtime(time.time() + seconds),
        )

    try:
        r = await client.post(TOKEN_URL, headers=_oidc_headers(), json=payload)
        r.raise_for_status()
        data = r.json()

        new_access = data.get("accessToken")
        new_refresh = data.get("refreshToken", account.get("refreshToken"))
        expires_at = _calc_expires_at(data.get("expiresIn"))
        now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())

        # Refresh successful: enable account, reset error count, update token
        await db.execute(
            """
            UPDATE accounts
            SET accessToken=?, refreshToken=?, expires_at=?, last_refresh_time=?, last_refresh_status=?,
                updated_at=?, enabled=1, error_count=0
            WHERE id=?
            """,
            (new_access, new_refresh, expires_at, now, "success", now, account_id),
        )
        print(f"  [OK] Account {label} refreshed successfully and re-enabled.")
        return True

    except httpx.HTTPError as e:
        error_detail = str(e)
        try:
            # Try to parse more detailed error info
            error_detail = e.response.json().get("error_description", str(e))
        except Exception:
            pass

        now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        await db.execute(
            "UPDATE accounts SET last_refresh_time=?, last_refresh_status=?, updated_at=? WHERE id=?",
            (now, "failed", now, account_id),
        )
        print(f"  [FAIL] Account {label} refresh failed: {error_detail}")
        return False
    except Exception as e:
        now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        await db.execute(
            "UPDATE accounts SET last_refresh_time=?, last_refresh_status=?, updated_at=? WHERE id=?",
            (now, "failed", now, account_id),
        )
        print(f"  [FAIL] Account {label} unexpected error: {e}")
        traceback.print_exc()
        return False


async def main():
    """Script main logic."""
    db = await init_db()

    # Find target accounts
    accounts_to_retry = await db.fetchall(
        "SELECT * FROM accounts WHERE enabled = 0 AND last_refresh_status = 'failed'"
    )
    accounts_to_retry = [row_to_dict(acc) for acc in accounts_to_retry]

    if not accounts_to_retry:
        print("No disabled accounts with refresh failure found.")
        await close_db()
        return

    print(f"Found {len(accounts_to_retry)} accounts to retry...")

    success_count = 0
    failure_count = 0

    # Create a shared HTTP client
    proxies = _get_proxies()
    mounts = None
    if proxies:
        proxy_url = proxies.get("https") or proxies.get("http")
        if proxy_url:
            mounts = {
                "https://": httpx.AsyncHTTPTransport(proxy=proxy_url),
                "http://": httpx.AsyncHTTPTransport(proxy=proxy_url),
            }

    async with httpx.AsyncClient(mounts=mounts, timeout=60.0) as client:
        for i, account in enumerate(accounts_to_retry):
            label = account.get("label") or account.get("id", "unknown ID")[:8]
            print(f"\n--- ({i+1}/{len(accounts_to_retry)}) Processing account: {label} ---")

            is_success = await refresh_single_account_token(db, account, client)
            if is_success:
                success_count += 1
            else:
                failure_count += 1

            # Add brief delay between accounts to avoid request concentration
            if i < len(accounts_to_retry) - 1:
                await asyncio.sleep(1)

    await close_db()

    print("\n--- Operation Completed ---")
    print(f"Successfully enabled: {success_count} accounts")
    print(f"Remaining disabled: {failure_count} accounts")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nOperation interrupted by user.")
        sys.exit(1)
