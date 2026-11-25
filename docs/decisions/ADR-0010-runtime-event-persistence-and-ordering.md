# ADR-0010: Runtime Event Persistence and Ordering

- **Status:** Accepted
- **Date:** 2026-11-25

## Context

Phase 1 emits typed `RuntimeEvent`s in memory. Phase 2 must persist event history durably with deterministic ordering that does not rely on wall-clock timestamps.

## Decision

1. **PersistedEvent Schema:** Store `event_id`, `session_id`, `task_id`, `run_id`, `event_type`, `timestamp` (UTC), `sequence_number`, `payload` (JSON), and `schema_version = 1`.
2. **Deterministic Sequence Numbers:** Assign monotonically increasing `sequence_number`s (`1, 2, ...`) scoped per `session_id` inside a `BEGIN IMMEDIATE` transaction on append. Enforce `UNIQUE(event_id)` and `UNIQUE(session_id, sequence_number)`.
3. **Paginated Retrieval:** Support querying by `session_id`, `task_id`, `run_id`, `event_type`, and `after_sequence` with bounded `limit` and `offset`.

## Consequences

- Total deterministic ordering within every Session, Task, and Run regardless of clock resolution.
- Duplicate `event_id` appends are rejected cleanly with `RepositoryError`.
