# ADR-0009: State Revision and Conflict Semantics

- **Status:** Accepted
- **Date:** 2026-11-25

## Context

Durable session-scoped and task-scoped key/value state may be updated concurrently by multiple tasks or callers. Silent last-write-wins overwrites would cause lost updates.

## Decision

1. **Strict JSON Validation:** State values must be JSON-serializable (`json.dumps(..., allow_nan=False)`). Arbitrary Python objects or `NaN`/`Infinity` floats are rejected with `StateError`.
2. **Optimistic Concurrency via Revisions:**
   - Creating a new key sets `revision = 1`.
   - Updating an existing key via `update(..., expected_revision=N)` succeeds only if the stored revision equals `N`, atomically incrementing it to `N + 1` inside a SQLite `BEGIN IMMEDIATE` transaction.
   - A mismatch raises `StateConflictError` so callers can re-read the latest state and retry deliberately.
   - `set(...)` without `expected_revision` performs an explicit upsert (creating at `revision = 1` or incrementing the current revision).

## Consequences

- Lost updates are prevented when callers use `expected_revision`.
- Namespace (`namespace`), `session_id`, and optional `task_id` scopes are strictly isolated.
