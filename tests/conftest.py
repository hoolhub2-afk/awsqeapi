import asyncio

import pytest


@pytest.fixture(scope="session", autouse=True)
def _cleanup_global_resources():
    """
    Ensure global resources (DB / HTTP clients) are closed after the test session.

    Without this, the aiosqlite worker thread can keep the Python process alive
    after tests finish, causing CI/local runs to hang.
    """
    yield

    async def _close_all() -> None:
        try:
            from src.core.http_client import close_global_client

            await close_global_client()
        except Exception:
            pass

        try:
            from src.core.database import close_db

            await close_db()
        except Exception:
            pass

    try:
        asyncio.run(_close_all())
    except RuntimeError:
        # If an event loop is already running (rare in pytest session teardown),
        # run cleanup on a dedicated loop.
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_close_all())
        finally:
            loop.close()
