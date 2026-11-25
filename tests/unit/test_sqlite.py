"""Unit tests for SQLite database, WAL mode, migrations, and transactions."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from orqen.config import StorageConfig
from orqen.exceptions import DatabaseError, MigrationError
from orqen.sessions import Session
from orqen.storage import Migration, SQLiteDatabase, SQLiteStorage, apply_migrations

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_sqlite_wal_foreign_keys_and_reopen(tmp_path: Path) -> None:
    db_path = tmp_path / "orqen.db"
    cfg = StorageConfig(database_path=db_path, busy_timeout_ms=5000)

    async with SQLiteStorage(cfg) as storage:
        journal = await storage.db.pragma_value("journal_mode")
        fk = await storage.db.pragma_value("foreign_keys")
        busy = await storage.db.pragma_value("busy_timeout")
        version = await storage.db.schema_version()

        assert str(journal).lower() == "wal"
        assert int(fk) == 1
        assert int(busy) == 5000
        assert version == 1
        await storage.db.integrity_check()

        session = await storage.sessions.create(Session(title="Persisted"))

    # Reopen and verify persistence
    async with SQLiteStorage(database_path=db_path) as reopened:
        loaded = await reopened.sessions.get(session.session_id)
        assert loaded is not None
        assert loaded.title == "Persisted"
        assert await reopened.db.schema_version() == 1


@pytest.mark.asyncio
async def test_uninitialized_db_raises_and_close_is_idempotent(tmp_path: Path) -> None:
    db = SQLiteDatabase(database_path=tmp_path / "uninit.db")
    with pytest.raises(DatabaseError) as exc_info:
        await db.schema_version()
    assert exc_info.value.code == "DATABASE_NOT_INITIALIZED"

    await db.initialize()
    await db.close()
    await db.close()  # idempotent


@pytest.mark.asyncio
async def test_unknown_future_schema_version_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "future.db"
    async with SQLiteDatabase(database_path=db_path) as db:
        await db.transaction(
            lambda conn: conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) "
                "VALUES (999, 'future_migration', '2026-01-01T00:00:00+00:00')"
            )
        )

    db2 = SQLiteDatabase(database_path=db_path)
    with pytest.raises(MigrationError) as exc_info:
        await db2.initialize()
    assert exc_info.value.code == "UNKNOWN_SCHEMA_VERSION"
    assert not db2.is_initialized


@pytest.mark.asyncio
async def test_migration_failure_rolls_back_cleanly(tmp_path: Path) -> None:
    db_path = tmp_path / "bad_mig.db"
    cfg = StorageConfig(database_path=db_path, auto_migrate=False)
    async with SQLiteDatabase(cfg) as db:
        bad_migrations = (
            Migration(
                version=1,
                name="broken_migration",
                statements=(
                    "CREATE TABLE temp_ok (id INTEGER PRIMARY KEY)",
                    "CREATE TABLE invalid syntax (",
                ),
            ),
        )
        with pytest.raises(MigrationError) as exc_info:
            await db.execute(lambda conn: apply_migrations(conn, bad_migrations))
        assert exc_info.value.code == "MIGRATION_FAILED"
        assert await db.schema_version() == 0

        def _table_exists(conn: object) -> bool:
            import sqlite3

            assert isinstance(conn, sqlite3.Connection)
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='temp_ok'"
            ).fetchone()
            return row is not None

        assert await db.execute(_table_exists) is False


@pytest.mark.asyncio
async def test_transaction_rollback_and_concurrent_writes(tmp_path: Path) -> None:
    db_path = tmp_path / "tx.db"
    async with SQLiteStorage(database_path=db_path) as storage:
        with pytest.raises(RuntimeError, match="boom"):

            def _failing_tx(conn: object) -> None:
                import sqlite3

                assert isinstance(conn, sqlite3.Connection)
                conn.execute(
                    """
                    INSERT INTO sessions (
                        session_id, title, agent_id, status, metadata_json,
                        revision, created_at, updated_at
                    ) VALUES ('sess_rollback', 'Nope', NULL, 'CREATED', '{}', 1,
                              '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
                    """
                )
                raise RuntimeError("boom")

            await storage.db.transaction(_failing_tx)

        assert await storage.sessions.get("sess_rollback") is None

        # Concurrent writes serialized cleanly
        created = await asyncio.gather(
            *[storage.sessions.create(Session(title=f"s-{i}")) for i in range(10)]
        )
        assert len(created) == 10
        listed = await storage.sessions.list(limit=20)
        assert len(listed) == 10
