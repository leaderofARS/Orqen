# ADR-0003: Agent Lifecycle and Reusability

- **Status:** Accepted
- **Date:** 2026-11-25

## Context

Agents in ORQEN must have explicit, validated lifecycle states and predictable behavior when executed by a `Runtime`. Allowing arbitrary state mutations or concurrent execution of a single stateful `Agent` instance without synchronization would introduce race conditions and hidden mutable state.

## Decision

1. **Explicit Lifecycle State Machine:** `AgentState` defines `CREATED`, `INITIALIZED`, `READY`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`, and `TERMINATED`. Transitions are centrally validated by a transition table; invalid transitions raise `LifecycleError`.
2. **Lifecycle Progression:**
   - An `Agent` starts in `CREATED` upon construction (or `READY` after initialization/validation). Specifically: `CREATED` -> `INITIALIZED` -> `READY` -> `RUNNING` -> (`COMPLETED` | `FAILED` | `CANCELLED`) -> `READY` (on reset for reuse) or `TERMINATED`. Any non-terminal state may transition to `TERMINATED`.
3. **Run Isolation & Concurrent Reuse:**
   - Execution state (message history, iteration count, tool call count, run ID) lives in a per-run `RunContext` inside `Runtime`, **not** on the `Agent` instance.
   - `Agent` holds only the immutable/validated definition (name, instructions, model, tool registry, `AgentConfig`) and its lifecycle state.
   - To keep `Agent.state` consistent and prevent concurrent mutation races, each `Agent` instance enforces single-active-run exclusivity via an `asyncio.Lock` (or non-reentrant state check `READY -> RUNNING`). Concurrent runs of the *same* `Agent` instance are rejected with `LifecycleError` unless separate `Agent` instances are constructed; multiple *different* agents run concurrently on the same `Runtime`.

## Consequences

- `Agent` instances are safely reusable sequentially (automatically resetting `COMPLETED`/`FAILED`/`CANCELLED` back to `READY` or via `agent.reset()`), while preventing accidental concurrent state corruption.
- Future phases (Phase 2+) can persist `RunContext` as `Session`/`Task` without refactoring `Agent` definitions.
