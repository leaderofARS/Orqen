"""Versioned SQLite schema migrations and integrity verification."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from orqen.exceptions import DatabaseError, MigrationError

if TYPE_CHECKING:
    from collections.abc import Sequence

SCHEMA_MIGRATIONS_TABLE = "schema_migrations"


@dataclass(frozen=True, slots=True)
class Migration:
    """Immutable, ordered SQLite schema migration."""

    version: int
    name: str
    statements: tuple[str, ...]


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        name="0001_initial_schema",
        statements=(
            """
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY,
                title TEXT,
                agent_id TEXT,
                status TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK (revision >= 1),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX idx_sessions_status ON sessions(status)",
            "CREATE INDEX idx_sessions_agent_id ON sessions(agent_id)",
            "CREATE INDEX idx_sessions_updated_at ON sessions(updated_at)",
            """
            CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                run_id TEXT,
                agent_id TEXT,
                agent_name TEXT NOT NULL,
                status TEXT NOT NULL,
                input TEXT NOT NULL,
                output TEXT,
                error_json TEXT,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                updated_at TEXT NOT NULL,
                duration_seconds REAL,
                revision INTEGER NOT NULL CHECK (revision >= 1)
            )
            """,
            "CREATE INDEX idx_tasks_session_id ON tasks(session_id)",
            "CREATE INDEX idx_tasks_status ON tasks(status)",
            "CREATE INDEX idx_tasks_agent_name ON tasks(agent_name)",
            "CREATE INDEX idx_tasks_created_at ON tasks(created_at)",
            """
            CREATE TABLE state_entries (
                namespace TEXT NOT NULL,
                session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                task_id TEXT REFERENCES tasks(task_id) ON DELETE CASCADE,
                key TEXT NOT NULL,
                value_json TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK (revision >= 1),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE UNIQUE INDEX idx_state_session_unique
            ON state_entries(namespace, session_id, key)
            WHERE task_id IS NULL
            """,
            """
            CREATE UNIQUE INDEX idx_state_task_unique
            ON state_entries(namespace, session_id, task_id, key)
            WHERE task_id IS NOT NULL
            """,
            "CREATE INDEX idx_state_scope ON state_entries(namespace, session_id, task_id)",
            """
            CREATE TABLE events (
                event_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                task_id TEXT REFERENCES tasks(task_id) ON DELETE CASCADE,
                run_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                sequence_number INTEGER NOT NULL CHECK (sequence_number >= 1),
                payload_json TEXT NOT NULL,
                schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
                UNIQUE(session_id, sequence_number)
            )
            """,
            "CREATE INDEX idx_events_session_seq ON events(session_id, sequence_number)",
            "CREATE INDEX idx_events_task_seq ON events(task_id, sequence_number)",
            "CREATE INDEX idx_events_run_seq ON events(run_id, sequence_number)",
            "CREATE INDEX idx_events_type ON events(event_type)",
            """
            CREATE TABLE artifacts (
                artifact_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                task_id TEXT REFERENCES tasks(task_id) ON DELETE CASCADE,
                filename TEXT NOT NULL,
                content_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
                sha256 TEXT NOT NULL,
                storage_key TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            )
            """,
            "CREATE INDEX idx_artifacts_session ON artifacts(session_id, created_at)",
            "CREATE INDEX idx_artifacts_task ON artifacts(task_id, created_at)",
        ),
    ),
)


def ensure_migrations_table(conn: sqlite3.Connection) -> None:
    """Create the `schema_migrations` tracking table if it does not exist."""
    try:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA_MIGRATIONS_TABLE} (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
    except sqlite3.Error as exc:
        raise MigrationError(
            f"Failed to initialize schema_migrations table: {exc}",
            code="MIGRATION_INIT_FAILED",
            cause=exc,
        ) from exc


def get_current_version(conn: sqlite3.Connection) -> int:
    """Return the highest applied migration version, or `0` for an uninitialized database."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (SCHEMA_MIGRATIONS_TABLE,),
    ).fetchone()
    if row is None:
        return 0
    max_row = conn.execute(
        f"SELECT COALESCE(MAX(version), 0) AS current_version FROM {SCHEMA_MIGRATIONS_TABLE}"
    ).fetchone()
    return int(max_row[0]) if max_row is not None else 0


def _validate_migration_sequence(migrations: Sequence[Migration]) -> None:
    seen: set[int] = set()
    prev = 0
    for migration in migrations:
        if migration.version <= 0:
            raise MigrationError(
                f"Migration version must be positive, got {migration.version}.",
                code="INVALID_MIGRATION_VERSION",
            )
        if migration.version in seen or migration.version <= prev:
            raise MigrationError(
                f"Migrations must be strictly ordered; invalid version {migration.version}.",
                code="UNORDERED_MIGRATIONS",
            )
        seen.add(migration.version)
        prev = migration.version


def apply_migrations(
    conn: sqlite3.Connection,
    migrations: Sequence[Migration] = MIGRATIONS,
) -> tuple[int, ...]:
    """Apply all pending migrations in order inside explicit transactions.

    Returns a tuple of newly applied migration versions.
    """
    _validate_migration_sequence(migrations)
    ensure_migrations_table(conn)

    applied_rows = conn.execute(
        f"SELECT version FROM {SCHEMA_MIGRATIONS_TABLE} ORDER BY version ASC"
    ).fetchall()
    applied_versions = {int(row[0]) for row in applied_rows}
    max_known = max((m.version for m in migrations), default=0)

    unknown = sorted(v for v in applied_versions if v > max_known)
    if unknown:
        raise MigrationError(
            f"Database contains unknown future schema version(s): {unknown}.",
            code="UNKNOWN_SCHEMA_VERSION",
            details={"unknown_versions": unknown, "max_supported_version": max_known},
        )

    newly_applied: list[int] = []
    for migration in migrations:
        if migration.version in applied_versions:
            continue
        try:
            conn.execute("BEGIN IMMEDIATE")
            for statement in migration.statements:
                conn.execute(statement)
            conn.execute(
                f"INSERT INTO {SCHEMA_MIGRATIONS_TABLE} (version, name, applied_at) "
                "VALUES (?, ?, ?)",
                (migration.version, migration.name, datetime.now(UTC).isoformat()),
            )
            conn.execute("COMMIT")
            newly_applied.append(migration.version)
        except sqlite3.Error as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise MigrationError(
                f"Migration {migration.version} ({migration.name}) failed: {exc}",
                code="MIGRATION_FAILED",
                details={"version": migration.version, "name": migration.name},
                cause=exc,
            ) from exc
    return tuple(newly_applied)


def check_integrity(conn: sqlite3.Connection) -> None:
    """Run SQLite `PRAGMA integrity_check` and raise `DatabaseError` on failure."""
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.Error as exc:
        raise DatabaseError(
            f"SQLite integrity check failed to execute: {exc}",
            code="INTEGRITY_CHECK_ERROR",
            cause=exc,
        ) from exc

    messages = [str(row[0]) for row in rows]
    if messages != ["ok"]:
        raise DatabaseError(
            f"SQLite integrity check reported errors: {messages}",
            code="INTEGRITY_CHECK_FAILED",
            details={"messages": messages},
        )
