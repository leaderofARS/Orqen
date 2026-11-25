# ADR-0008: SQLite Backend, WAL, and Connection Strategy

- **Status:** Accepted
- **Date:** 2026-11-25

## Context

ORQEN's runtime is async-first (`asyncio`), whereas Python's standard-library `sqlite3` is synchronous and binds connections to the creating thread by default. Blocking the event loop with SQLite I/O is unacceptable.

## Decision

1. **Zero-Dependency Async Access:** Use standard-library `sqlite3` executed on a dedicated single-worker `ThreadPoolExecutor(max_workers=1)` owned by `SQLiteDatabase`. This guarantees thread affinity for the `sqlite3.Connection`, serializes write transactions cleanly, and never blocks the `asyncio` event loop.
2. **SQLite Pragmas:** Enable `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`, `PRAGMA busy_timeout=<ms>`, and `PRAGMA synchronous=NORMAL` on initialization.
3. **Versioned Migrations:** Manage schema evolution via an ordered `schema_migrations` table (`0001_initial_schema`) with transactional execution, unknown-future-version rejection (`MigrationError`), and `PRAGMA integrity_check`.

## Consequences

- No external database driver dependency required in Phase 2.
- Deterministic write serialization and clean shutdown via executor teardown.
- Future high-concurrency multi-process deployments will target PostgreSQL (Phase 6+).
