# Persistence, Sessions, Tasks, State & Artifacts Guide (`v0.2.0`)

Phase 2 adds durable, async-safe SQLite persistence to ORQEN while keeping Phase 1's standalone `Runtime.run(agent, input=...)` behavior intact.

## 1. SQLite Configuration (`StorageConfig` & `SQLiteStorage`)

`SQLiteStorage` manages a dedicated single-worker `ThreadPoolExecutor` (`orqen-sqlite`) so standard-library `sqlite3` operations never block the `asyncio` event loop.

Default pragmas applied on `initialize()`:
- `PRAGMA foreign_keys = ON`
- `PRAGMA journal_mode = WAL`
- `PRAGMA synchronous = NORMAL`
- `PRAGMA busy_timeout = 5000`
- Versioned migrations (`schema_migrations`, starting at `0001_initial_schema`) and `PRAGMA integrity_check`.

Configuration via `StorageConfig` or environment variables (`ORQEN_STORAGE__ENABLED`, `ORQEN_STORAGE__DATABASE_PATH`, `ORQEN_STORAGE__BUSY_TIMEOUT_MS`):

```python
from orqen.config import load_config
from orqen.storage import SQLiteStorage

cfg = load_config(overrides={"storage": {"enabled": True, "database_path": "./orqen.db"}})
async with SQLiteStorage(cfg.storage) as storage:
    ...
```

## 2. Sessions (`SessionManager`)

A `Session` represents a durable multi-task execution context with explicit lifecycle states:
- `CREATED` → `ACTIVE` | `CLOSED`
- `ACTIVE` → `IDLE` | `CLOSED`
- `IDLE` → `ACTIVE` | `CLOSED`
- `CLOSED` → (terminal; immutable)

Key operations on `SessionManager`:
- `create()`, `get()`, `require()`, `require_open()`
- `activate()`, `mark_idle()`, `close()`, `delete()`
- `rename()`, `update_metadata()`
- `list(status=..., agent_id=..., limit=..., offset=...)`
- `get_tasks()`, `get_events()`, `get_artifacts()`

## 3. Tasks (`TaskManager`)

Each discrete execution request within a `Session` creates a `Task` with explicit transitions:
- `PENDING` → `RUNNING` | `CANCELLED`
- `RUNNING` → `COMPLETED` | `FAILED` | `CANCELLED` | `TIMED_OUT`
- Terminal states (`COMPLETED`, `FAILED`, `CANCELLED`, `TIMED_OUT`) are immutable.

`TaskManager` sanitizes persisted errors using ORQEN's secret-redaction rules before storing them.

## 4. Persistent State (`StateManager`)

`StateManager` provides strictly JSON-validated, namespace-isolated, session- or task-scoped key/value state with optimistic concurrency:
- `set()` creates a new key at `revision = 1` or upserts when `expected_revision` is omitted.
- `update(..., expected_revision=N)` atomically checks `revision == N` inside a `BEGIN IMMEDIATE` transaction and increments to `N + 1`, raising `StateConflictError` on mismatch.
- Non-JSON values (`NaN`, `Infinity`, custom Python objects) are rejected with `StateError`.

## 5. Event History (`EventRepository`)

When `Runtime(..., storage=storage)` executes with `session=...`, typed `RuntimeEvent`s (excluding incremental `ModelStreamChunk` deltas) are persisted as `PersistedEvent` records with monotonic `sequence_number`s scoped per `session_id`.

## 6. ArtifactStore (`ArtifactStore`)

`ArtifactStore` stores payload bytes on disk under `<directory>/<session_id>/<artifact_id>` using temporary-file writes + `os.replace`, while storing `ArtifactMetadata` (including `sha256` checksum and `size_bytes`) in SQLite. Path traversal characters and symlinks are rejected with `ArtifactError`.
