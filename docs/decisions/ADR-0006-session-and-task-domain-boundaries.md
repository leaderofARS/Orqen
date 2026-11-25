# ADR-0006: Session and Task Domain Boundaries

- **Status:** Accepted
- **Date:** 2026-11-25

## Context

Phase 1 executed single-agent runs ephemerally. Phase 2 introduces durable multi-turn execution contexts without conflating a long-lived conversation/context with a single execution attempt.

## Decision

1. **Session (`orqen.sessions`):** Represents a durable conversation/execution context (`CREATED` → `ACTIVE` → `IDLE` → `CLOSED`) that can span multiple tasks, state entries, events, and artifacts. Closed sessions are immutable and reject new tasks or mutations.
2. **Task (`orqen.tasks`):** Represents a single discrete execution request within a Session (`PENDING` → `RUNNING` → `COMPLETED` | `FAILED` | `CANCELLED` | `TIMED_OUT`). Terminal states are immutable. Distinct execution attempts create new `Task` records rather than mutating terminal tasks.
3. **Runtime Integration:** `Runtime.run(..., session=...)` and `Runtime.stream(..., session=...)` optionally bind an execution to a `Session`, creating and tracking a `Task` automatically while preserving standalone Phase 1 execution when `session=None`.

## Consequences

- Clean separation between long-lived `Session` identity, per-execution `Task` lifecycle, and `Runtime` execution mechanics.
- No automatic retry or crash-recovery loops are introduced in Phase 2 (deferred to Phase 4).
