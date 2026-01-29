import asyncio
from pathlib import Path

import pytest

from src.security.advanced import create_key_manager, SecurityLevel
from src.core.database import SQLiteBackend


@pytest.mark.asyncio
async def test_key_usage_persisted_and_enforced(tmp_path: Path):
    db_path = tmp_path / "keys.sqlite3"
    backend = SQLiteBackend(db_path)
    await backend.initialize()
    try:
        manager = create_key_manager(SecurityLevel.PRODUCTION, backend)
        key_id, api_key = manager.generate_secure_key(max_uses=2)
        await manager.save_key_to_db(key_id)

        assert await manager.verify_key(api_key, "127.0.0.1", "pytest-client") is not None
        row = await backend.fetchone("SELECT usage_count FROM secure_keys WHERE key_id=?", (key_id,))
        assert row["usage_count"] == 1

        # Simulate restart
        manager2 = create_key_manager(SecurityLevel.PRODUCTION, backend)
        manager2.set_database(backend)
        await manager2.load_keys_from_db()

        assert await manager2.verify_key(api_key, "127.0.0.1", "pytest-client") is not None
        assert await manager2.verify_key(api_key, "127.0.0.1", "pytest-client") is None

        row = await backend.fetchone("SELECT status, usage_count FROM secure_keys WHERE key_id=?", (key_id,))
        assert row["usage_count"] == 2
        assert row["status"] == "inactive"
    finally:
        await backend.close()
